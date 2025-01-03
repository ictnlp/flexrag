from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL.Image import Image

from flexrag.prompt import ChatPrompt, MultiModelChatPrompt
from flexrag.utils import Register, LOGGER_MANAGER


logger = LOGGER_MANAGER.get_logger("flexrag.models")


@dataclass
class GeneratorBaseConfig: ...


@dataclass
class GenerationConfig:
    do_sample: bool = True
    sample_num: int = 1
    temperature: float = 1.0
    max_new_tokens: int = 512
    top_p: float = 0.9
    top_k: int = 50
    eos_token_id: Optional[int] = None
    stop_str: list[str] = field(default_factory=list)

    def __post_init__(self):
        # check values
        assert self.sample_num > 0, "sample_num must be greater than 0"
        if self.sample_num > 1:
            assert self.do_sample, "do_sample must be True when sample_num > 1"
        assert self.temperature >= 0, "temperature must be greater than or equal to 0"
        assert self.max_new_tokens > 0, "max_new_tokens must be greater than 0"
        assert 0 <= self.top_p <= 1, "top_p must be between 0 and 1"
        assert self.top_k > 0, "top_k must be greater than 0"


class GeneratorBase(ABC):
    @abstractmethod
    def chat(
        self,
        prompts: list[ChatPrompt],
        generation_config: GenerationConfig = None,
    ) -> list[list[str]]:
        """chat with the model using model templates.

        :param prompts: A batch of ChatPrompts.
        :param generation_config: GenerationConfig. Defaults to None.
        :type prompts: list[ChatPrompt]
        :type generation_config: GenerationConfig
        :return: A batch of chat responses.
        :rtype: list[list[str]]
        """
        return

    async def async_chat(
        self,
        prompts: list[ChatPrompt],
        generation_config: GenerationConfig = None,
    ) -> list[list[str]]:
        """The async version of chat."""
        logger.warning(
            "Current encoder does not support asyncronous chat, thus the code will be run in syncronous mode"
        )
        return self.chat(prompts=prompts, generation_config=generation_config)

    @abstractmethod
    def generate(
        self,
        prefixes: list[str],
        generation_config: GenerationConfig = None,
    ) -> list[list[str]]:
        """generate text with the model using the given prefixes.

        :param prefixes: A batch of prefixes.
        :param generation_config: GenerationConfig. Defaults to None.
        :type prefixes: list[str]
        :type generation_config: GenerationConfig
        :return: A batch of generated text.
        :rtype: list[list[str]]
        """
        return

    async def async_generate(
        self,
        prefixes: list[str],
        generation_config: GenerationConfig = None,
    ) -> list[list[str]]:
        """The async version of generate."""
        logger.warning(
            "Current generator does not support asyncronous generate, thus the code will be run in syncronous mode"
        )
        return self.generate(prefixes=prefixes, generation_config=generation_config)


class VLMGeneratorBase(GeneratorBase):
    @abstractmethod
    def chat(
        self,
        prompts: list[MultiModelChatPrompt],
        generation_config: GenerationConfig = None,
    ) -> list[list[str]]:
        """chat with the model using model templates.

        :param prompts: A batch of MultiModelChatPrompts.
        :param generation_config: GenerationConfig. Defaults to None.
        :type prompts: list[MultiModelChatPrompt]
        :type generation_config: GenerationConfig
        :return: A batch of chat responses.
        :rtype: list[list[str]]
        """
        return

    async def async_chat(
        self,
        prompts: list[MultiModelChatPrompt],
        generation_config: GenerationConfig = None,
    ) -> list[list[str]]:
        """The async version of chat."""
        logger.warning(
            "Current encoder does not support asyncronous chat, thus the code will be run in syncronous mode"
        )
        return self.chat(prompts=prompts, generation_config=generation_config)

    @abstractmethod
    def generate(
        self,
        prefixes: list[str],
        images: list[Image],
        generation_config: GenerationConfig = None,
    ) -> list[list[str]]:
        """generate text with the model using the given prefixes.

        :param prefixes: A batch of prefixes.
        :param images: A batch of images.
        :param generation_config: GenerationConfig. Defaults to None.
        :type prefixes: list[str]
        :type images: list[Image]
        :type generation_config: GenerationConfig
        :return: A batch of generated text.
        :rtype: list[list[str]]
        """
        return

    async def async_generate(
        self,
        prefixes: list[str],
        images: list[Image],
        generation_config: GenerationConfig = None,
    ) -> list[list[str]]:
        """The async version of generate."""
        logger.warning(
            "Current generator does not support asyncronous generate, thus the code will be run in syncronous mode"
        )
        return self.generate(
            prefixes=prefixes, images=images, generation_config=generation_config
        )


@dataclass
class EncoderBaseConfig: ...


class EncoderBase(ABC):
    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """encode the given texts into embeddings.

        :param texts: A batch of texts.
        :type texts: list[str]
        :return: A batch of embeddings.
        :rtype: np.ndarray
        """
        return

    async def async_encode(self, texts: list[str]) -> np.ndarray:
        """The async version of encode."""
        logger.warning(
            "Current encoder does not support asyncronous encode, thus the code will be run in syncronous mode"
        )
        return self.encode(texts)

    @property
    @abstractmethod
    def embedding_size(self) -> int:
        return


GENERATORS = Register[GeneratorBase]("generator")
ENCODERS = Register[EncoderBase]("encoder")