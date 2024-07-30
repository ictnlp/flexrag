import json
from dataclasses import dataclass, field
from typing import Optional
from os import PathLike

from kylin.utils import Choices


@dataclass
class ChatTurn:
    role: Choices(["user", "assistant", "system"])  # type: ignore
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, chat_turn: dict[str, str]):
        return cls(role=chat_turn["role"], content=chat_turn["content"])


@dataclass
class ChatPrompt:
    system: Optional[ChatTurn] = None
    history: list[ChatTurn] = field(default_factory=list)
    demonstrations: list[list[ChatTurn]] = field(default_factory=list)

    def __init__(
        self,
        system: Optional[str | ChatTurn] = None,
        history: list[ChatTurn] | list[dict[str, str]] = [],
        demonstrations: list[list[ChatTurn]] | list[list[dict[str, str]]] = [],
    ):
        # set system
        if isinstance(system, str):
            system = ChatTurn(role="system", content=system)
        self.system = system

        # set history
        if len(history) > 0:
            if isinstance(history[0], dict):
                history = [ChatTurn.from_dict(turn) for turn in history]
        self.history = history

        # set demonstrations
        if len(demonstrations) > 0:
            if isinstance(demonstrations[0][0], dict):
                demonstrations = [
                    [ChatTurn.from_dict(turn) for turn in demo]
                    for demo in demonstrations
                ]
        self.demonstrations = demonstrations
        return

    def to_list(self) -> list[dict[str, str]]:
        data = []
        if self.system is not None:
            data.append({"role": "system", "content": self.system.content})
        for demo in self.demonstrations:
            for turn in demo:
                data.append(turn.to_dict())
        for turn in self.history:
            data.append(turn.to_dict())
        return data

    def to_json(self, path: str | PathLike):
        data = {"system": self.system.to_dict(), "history": [], "demonstrations": []}
        for turn in self.history:
            data["history"].append(turn.to_dict())
        for demo in self.demonstrations:
            data["demonstrations"].append([turn.to_dict() for turn in demo])
        with open(path, "w") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return

    @classmethod
    def from_list(cls, prompt: list[dict[str, str]]) -> "ChatPrompt":
        history = [ChatTurn.from_dict(turn) for turn in prompt]
        if history[0].role == "system":
            system = history.pop(0)
        else:
            system = None
        return cls(system=system, history=history, demonstrations=[])

    @classmethod
    def from_json(cls, path: str | PathLike) -> "ChatPrompt":
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return cls.from_list(data)
        return cls(
            system=ChatTurn.from_dict(data["system"]),
            history=[ChatTurn.from_dict(turn) for turn in data["history"]],
            demonstrations=[
                [ChatTurn.from_dict(turn) for turn in demo]
                for demo in data["demonstrations"]
            ],
        )

    def load_demonstrations(self, demo_path: str | PathLike):
        with open(demo_path, "r") as f:
            data = json.load(f)
        self.demonstrations = [
            [ChatTurn.from_dict(turn) for turn in demo] for demo in data
        ]
        return

    def pop_history(self, n: int) -> ChatTurn:
        return self.history.pop(n)

    def pop_demonstration(self, n: int) -> list[ChatTurn]:
        return self.demonstrations.pop(n)

    def update(self, chat_turn: ChatTurn | list[ChatTurn]):
        self.history.append(chat_turn)
        return

    def clear(self, clear_system: bool = False):
        if clear_system:
            self.system = None
        self.history = []
        self.demonstrations = []
        return

    def __len__(self) -> int:
        system_num = 0 if self.system is None else 1
        history_num = len(self.history)
        demo_num = sum([len(demo) for demo in self.demonstrations])
        return system_num + history_num + demo_num