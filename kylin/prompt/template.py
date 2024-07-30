from abc import ABC, abstractmethod
from copy import deepcopy
from functools import partial
from typing import Optional

from transformers import PreTrainedModel, PreTrainedTokenizer
from vllm import LLM

from kylin.utils import Choices

from .prompt_base import ChatPrompt

TRUNCATION_STRATEGIES = ["left", "right", "history", "demo", "auto"]


class Template(ABC):
    @abstractmethod
    def render_to_text(
        self,
        prompt: ChatPrompt,
        add_generation_prompt: bool = True,
    ) -> str:
        return

    @abstractmethod
    def render_to_ids(
        self,
        prompt: ChatPrompt,
        max_length: int = None,
        truncation: Choices(TRUNCATION_STRATEGIES) = "auto",  # type: ignore
        padding: bool = False,
        has_label: bool = False,
        add_generation_prompt: bool = True,
    ):
        return


class HFTemplate(Template):
    def __init__(
        self,
        tokenizer: PreTrainedTokenizer,
        sys_prompt: str = None,
        chat_template: str = None,  # Jinja template
    ) -> None:
        super().__init__()
        self.tokenizer = tokenizer
        self.sys_prompt = sys_prompt
        self.chat_template = chat_template
        return

    def render_to_text(
        self,
        prompt: ChatPrompt,
        add_generation_prompt: bool = True,
    ) -> str:
        # add default system prompt
        prompt_ = prompt.to_list()
        if (len(prompt_) == 0) and (self.sys_prompt is not None):
            prompt_.append({"role": "system", "content": self.sys_prompt})
        if (prompt_[0]["role"] != "system") and (self.sys_prompt is not None):
            prompt_.insert(0, {"role": "system", "content": self.sys_prompt})
        # apply template
        prefix = self.tokenizer.apply_chat_template(
            prompt_,
            tokenize=False,
            add_generation_prompt=add_generation_prompt,
        )
        return prefix

    def render_to_ids(
        self,
        prompt: ChatPrompt,
        max_length: Optional[int] = None,
        truncation: Choices(TRUNCATION_STRATEGIES) = "auto",  # type: ignore
        padding: bool = False,
        has_label: bool = False,  # TODO: support label(mask_input)
        add_generation_prompt: bool = True,
    ) -> list[int] | tuple[list[int], list[int]]:
        def _encode(
            prompt: ChatPrompt,
            max_length: int = None,
            truncation: bool = False,
            truncation_side: str = "left",
        ) -> list[int]:
            prefix = self.render_to_text(prompt, add_generation_prompt)
            self.tokenizer.truncation_side = truncation_side
            ids = self.tokenizer(
                prefix,
                max_length=max_length,
                padding=padding,
                truncation=truncation,
            )["input_ids"]
            return ids

        # truncate the prompt
        prompt = deepcopy(prompt)
        if max_length is not None:
            match truncation:
                case "left":
                    ids = _encode(
                        prompt,
                        max_length=max_length,
                        truncation=True,
                        truncation_side="left",
                    )
                case "right":
                    ids = _encode(
                        prompt,
                        max_length=max_length,
                        truncation=True,
                        truncation_side="right",
                    )
                case "history":
                    pre_ids = _encode(prompt)
                    while len(pre_ids) > max_length:
                        if len(prompt.history) > 1:
                            prompt.pop_history(0)
                            prompt.pop_history(0)
                        else:
                            raise ValueError(
                                "Unable to truncate the prompt using `history` strategy."
                            )
                        pre_ids = _encode(prompt)
                    ids = pre_ids
                case "demo":
                    pre_ids = _encode(prompt)
                    while len(pre_ids) > max_length:
                        if len(prompt.demonstrations) > 0:
                            prompt.pop_demonstration(0)
                        else:
                            raise ValueError(
                                "Unable to truncate the prompt using `demo` strategy."
                            )
                case "auto":
                    pre_ids = _encode(prompt)
                    while len(pre_ids) > max_length:
                        if len(prompt.history) > 1:
                            prompt.pop_history(0)
                            prompt.pop_history(0)
                            pre_ids = _encode(prompt)
                        elif len(prompt.demonstrations) > 0:
                            prompt.pop_demonstration(0)
                            pre_ids = _encode(prompt)
                        else:
                            pre_ids = _encode(
                                prompt, truncation=True, truncation_side="left"
                            )
                    ids = pre_ids
                case _:
                    raise ValueError("Unsupported truncation strategy.")
        else:
            ids = _encode(prompt)
        return ids


# register default templates
Llama3Template = partial(
    HFTemplate,
    sys_prompt="You are a pirate chatbot who always responds in pirate speak!",
)

Qwen2Template = partial(
    HFTemplate,
    sys_prompt="You are a helpful assistant.",
)

Phi3Template = HFTemplate


def load_template(
    model: LLM | PreTrainedModel,
    tokenizer: PreTrainedTokenizer,
) -> Template:
    # get cfg name
    if isinstance(model, LLM):
        cfg_name = model.llm_engine.model_config.hf_config.__class__.__name__
    elif isinstance(model, PreTrainedModel):
        cfg_name = model.config.__class__.__name__

    # load template
    match cfg_name:
        case "Phi3Config":
            template = Phi3Template(tokenizer=tokenizer)
        case "Phi3SmallConfig":
            template = Phi3Template(tokenizer=tokenizer)
        case "LlamaConfig":
            template = Llama3Template(tokenizer=tokenizer)
        case "Qwen2Config":
            template = Qwen2Template(tokenizer=tokenizer)
        case _:
            raise ValueError(f"Unsupported config: {cfg_name}")
    return template