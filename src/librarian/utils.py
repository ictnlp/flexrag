import importlib
import json
import logging
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from csv import reader
from dataclasses import field, make_dataclass
from enum import Enum
from functools import partial
from glob import glob
from itertools import zip_longest
from multiprocessing import Manager
from time import perf_counter
from typing import Coroutine, Generic, Iterable, Iterator, Optional, TypeVar

import numpy as np
from omegaconf import MISSING, DictConfig, ListConfig, OmegaConf


class SimpleProgressLogger:
    def __init__(self, logger: logging.Logger, total: int = None, interval: int = 100):
        self.total = total
        self.interval = interval
        self.logger = logger
        self.current = 0
        self.current_stage = 0
        self.desc = "Progress"
        self.start_time = perf_counter()
        return

    def update(self, step: int = 1, desc: str = None) -> None:
        if desc is not None:
            self.desc = desc
        self.current += step
        stage = self.current // self.interval
        if stage > self.current_stage:
            self.current_stage = stage
            self.log()
        return

    def log(self) -> None:
        def fmt_time(time: float) -> str:
            if time < 60:
                return f"{time:.2f}s"
            if time < 3600:
                return f"{time//60:02.0f}:{time%60:02.0f}"
            else:
                return f"{time//3600:.0f}:{(time%3600)//60:02.0f}:{time%60:02.0f}"

        if (self.total is not None) and (self.current < self.total):
            time_spend = perf_counter() - self.start_time
            time_left = time_spend * (self.total - self.current) / self.current
            speed = self.current / time_spend
            num_str = f"{self.current} / {self.total}"
            percent_str = f"({self.current/self.total:.2%})"
            time_str = f"[{fmt_time(time_spend)} / {fmt_time(time_left)}, {speed:.2f} update/s]"
            self.logger.info(f"{self.desc}: {num_str} {percent_str} {time_str}")
        else:
            time_spend = perf_counter() - self.start_time
            speed = self.current / time_spend
            num_str = f"{self.current}"
            time_str = f"[{fmt_time(time_spend)}, {speed:.2f} update/s]"
            self.logger.info(f"{self.desc}: {num_str} {time_str}")
        return


RegisterBaseClass = TypeVar("RegisterBaseClass")


class Register(Generic[RegisterBaseClass]):
    def __init__(self, register_name: str = None):
        self.name = register_name
        self._items = {}
        self._shortcuts = {}
        return

    def __call__(self, *short_names: str, config_class=None):
        def registe_item(item):
            main_name = str(item).split(".")[-1][:-2]
            # check name conflict
            assert main_name not in self._items, f"Name Conflict {main_name}"
            assert main_name not in self._shortcuts, f"Name Conflict {main_name}"
            for name in short_names:
                assert name not in self._items, f"Name Conflict {name}"
                assert name not in self._shortcuts, f"Name Conflict {name}"

            # register the item
            self._items[main_name] = {
                "item": item,
                "main_name": main_name,
                "short_names": short_names,
                "config_class": config_class,
            }
            for name in short_names:
                self._shortcuts[name] = main_name
            return item

        return registe_item

    def __iter__(self):
        return self._items.__iter__()

    @property
    def names(self) -> list[str]:
        return list(self._items.keys()) + list(self._shortcuts.keys())

    @property
    def mainnames(self) -> list[str]:
        return list(self._items.keys())

    @property
    def shortnames(self) -> list[str]:
        return list(self._shortcuts.keys())

    def __getitem__(self, key: str) -> dict:
        if key not in self._items:
            key = self._shortcuts[key]
        return self._items[key]

    def get(self, key: str, default=None) -> dict:
        if key not in self._items:
            if key not in self._shortcuts:
                return default
            key = self._shortcuts[key]
        return self._items[key]

    def get_item(self, key: str):
        if key not in self._items:
            key = self._shortcuts[key]
        return self._items[key]["item"]

    def make_config(
        self,
        allow_multiple: bool = False,
        default=MISSING,
        config_name: str = None,
    ):
        choice_name = f"{self.name}_type"
        config_name = f"{self.name}_config" if config_name is None else config_name
        if allow_multiple:
            config_fields = [
                (
                    choice_name,
                    list[Choices(self.names)],
                    field(default_factory=list),
                )
            ]
        else:
            config_fields = [
                (
                    choice_name,
                    Optional[Choices(self.names)],
                    field(default=default),
                )
            ]
        config_fields += [
            (
                f"{self[name]['short_names'][0]}_config",
                self[name]["config_class"],
                field(default_factory=self._items[name]["config_class"]),
            )
            for name in self.mainnames
            if self[name]["config_class"] is not None
        ]
        return make_dataclass(config_name, config_fields)

    def load(
        self, config: DictConfig, **kwargs
    ) -> RegisterBaseClass | list[RegisterBaseClass]:
        choice = getattr(config, f"{self.name}_type", None)
        if choice is None:
            return None
        if isinstance(choice, (list, ListConfig)):
            loaded = []
            for name in choice:
                if name in self:
                    cfg_name = f"{self[name]['short_names'][0]}_config"
                    sub_cfg = getattr(config, cfg_name, None)
                    if sub_cfg is None:
                        loaded.append(self[name]["item"](**kwargs))
                    else:
                        loaded.append(self[name]["item"](sub_cfg, **kwargs))
        elif choice in self:
            cfg_name = f"{self[choice]['short_names'][0]}_config"
            sub_cfg = getattr(config, cfg_name, None)
            if sub_cfg is None:
                loaded = self[choice]["item"](**kwargs)
            else:
                loaded = self[choice]["item"](sub_cfg, **kwargs)
        else:
            raise ValueError(f"Invalid {self.name} type: {choice}")
        return loaded

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: str) -> bool:
        return key in self.names

    def __str__(self) -> str:
        data = {
            "name": self.name,
            "items": [
                {
                    "main_name": k,
                    "short_names": v["short_names"],
                    "config_class": str(v["config_class"]),
                }
                for k, v in self._items.items()
            ],
        }
        return json.dumps(data, indent=4)

    def __repr__(self) -> str:
        return str(self)

    def __add__(self, register: "Register"):
        new_register = Register()
        new_register._items = {**self._items, **register._items}
        new_register._shortcuts = {**self._shortcuts, **register._shortcuts}
        return new_register


@contextmanager
def set_env_var(key, value):
    original_value = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if original_value is None:
            del os.environ[key]
        else:
            os.environ[key] = original_value


class StrEnum(Enum):
    def __eq__(self, other: str):
        return self.value == other

    def __str__(self):
        return self.value

    def __hash__(self):
        return hash(self.value)

    def __repr__(self):
        return self.value


def Choices(choices: Iterable[str]):
    return StrEnum("Choices", {c: c for c in choices})


# Monkey Patching the JSONEncoder to handle StrEnum
class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, StrEnum):
            return str(obj)
        if isinstance(obj, DictConfig):
            return OmegaConf.to_container(obj, resolve=True)
        if isinstance(obj, np.int64):
            return int(obj)
        if isinstance(obj, np.int32):
            return int(obj)
        if isinstance(obj, np.float64):
            return float(obj)
        if isinstance(obj, np.float32):
            return float(obj)
        if hasattr(obj, "to_list"):
            return obj.to_list()
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return super().default(obj)


json.dumps = partial(json.dumps, cls=CustomEncoder)
json.dump = partial(json.dump, cls=CustomEncoder)


class TimeMeter:
    def __init__(self):
        self._manager = Manager()
        self.timers = self._manager.dict()
        return

    def __call__(self, *timer_names: str):
        def time_it(func):
            def wrapper(*args, **kwargs):
                start_time = perf_counter()
                result = func(*args, **kwargs)
                end_time = perf_counter()
                if timer_names not in self.timers:
                    self.timers[timer_names] = self._manager.list()
                self.timers[timer_names].append(end_time - start_time)
                return result

            async def async_wrapper(*args, **kwargs):
                start_time = perf_counter()
                result = await func(*args, **kwargs)
                end_time = perf_counter()
                if timer_names not in self.timers:
                    self.timers[timer_names] = self._manager.list()
                self.timers[timer_names].append(end_time - start_time)
                return result

            if isinstance(func, Coroutine):
                return async_wrapper
            return wrapper

        return time_it

    @property
    def statistics(self) -> list[dict[str, float]]:
        statistics = []
        for k, v in self.timers.items():
            v = list(v)
            statistics.append(
                {
                    "name": k,
                    "calls": len(v),
                    "average call time": np.mean(v),
                    "total time": np.sum(v),
                }
            )
        return statistics

    @property
    def details(self) -> dict:
        return {k: v for k, v in self.timers.items()}


TIME_METER = TimeMeter()


class LoggerManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if not cls._instance:
            with cls._lock:  # ensure thread safety
                if not cls._instance:
                    cls._instance = super(LoggerManager, cls).__new__(cls)
                    cls._instance._configure()  # initialize the LoggerManager
        return cls._instance

    def _configure(self):
        self.loggers: dict[str, logging.Logger] = {}
        logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
        return

    def get_logger(self, name: str) -> logging.Logger:
        if name not in self.loggers:
            self.loggers[name] = logging.getLogger(name)
        return self.loggers[name]

    def add_handler(self, handler: logging.Handler, name: str = None):
        if name is None:
            for logger in self.loggers.values():
                logger.addHandler(handler)
        else:
            logger = self.get_logger(name)
            logger.addHandler(handler)
        return

    def remove_handler(self, handler: logging.Handler, name: str = None):
        if name is None:
            for logger in self.loggers.values():
                logger.removeHandler(handler)
        else:
            logger = self.get_logger(name)
            logger.removeHandler(handler)
        return

    def set_level(self, level: int, name: str = None):
        if name is None:
            for logger in self.loggers.values():
                logger.setLevel(level)
        else:
            logger = self.get_logger(name)
            logger.setLevel(level)
        return


LOGGER_MANAGER = LoggerManager()


try:
    COMMIT_ID = (
        subprocess.check_output(
            ["git", "-C", f"{os.path.dirname(__file__)}", "rev-parse", "HEAD"]
        )
        .strip()
        .decode("utf-8")
    )
except:
    COMMIT_ID = "Unknown"


def load_user_module(module_path: str):
    module_path = os.path.abspath(module_path)
    module_parent, module_name = os.path.split(module_path)
    if module_name not in sys.modules:
        sys.path.insert(0, module_parent)
        importlib.import_module(module_name)