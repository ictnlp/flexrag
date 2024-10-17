import logging
import os
from copy import deepcopy
from dataclasses import dataclass

from kylin.prompt import ChatTurn, ChatPrompt
from kylin.models import (
    GenerationConfig,
    GeneratorConfig,
    load_generator,
)
from kylin.retriever import RetrievedContext
from kylin.utils import Choices

logger = logging.getLogger("Assistant")


@dataclass
class AssistantConfig(GeneratorConfig, GenerationConfig):
    adaptive_retrieval: bool = False
    response_type: Choices(["short", "long", "original"]) = "short"  # type: ignore


class Assistant:
    def __init__(self, cfg: AssistantConfig):
        # set basic args
        self.adaptive_retrieval = cfg.adaptive_retrieval
        self.gen_cfg = cfg
        if self.gen_cfg.sample_num > 1:
            logger.warning("Sample num > 1 is not supported for Assistant")
            self.gen_cfg.sample_num = 1

        # load generator
        self.generator = load_generator(cfg)

        # load prompts
        match cfg.response_type:
            case "short":
                self.prompt_with_ctx = ChatPrompt.from_json(
                    os.path.join(
                        os.path.dirname(__file__),
                        "assistant_prompts",
                        "shortform_generate_prompt_with_context.json",
                    )
                )
                self.prompt_wo_ctx = ChatPrompt.from_json(
                    os.path.join(
                        os.path.dirname(__file__),
                        "assistant_prompts",
                        "shortform_generate_prompt_without_context.json",
                    )
                )
            case "long":
                self.prompt_with_ctx = ChatPrompt.from_json(
                    os.path.join(
                        os.path.dirname(__file__),
                        "assistant_prompts",
                        "longform_generate_prompt_with_context.json",
                    )
                )
                self.prompt_wo_ctx = ChatPrompt.from_json(
                    os.path.join(
                        os.path.dirname(__file__),
                        "assistant_prompts",
                        "longform_generate_prompt_without_context.json",
                    )
                )
            case "original":
                self.prompt_with_ctx = ChatPrompt()
                self.prompt_wo_ctx = ChatPrompt()
        return

    def answer(
        self, questions: list[str], contexts: list[list[RetrievedContext]]
    ) -> tuple[list[str], list[ChatPrompt]]:
        """Answer question with given contexts

        Args:
            question (list[str]): A list of questions to answer.
            contexts (list[list[RetrievedContext]]): A list of contexts searched by the searcher.

        Returns:
            responses (list[str]): Responses to the questions
            prompt (list[ChatPrompt]): The used prompts.
        """
        if self.adaptive_retrieval:
            use_contexts = self.decide_search(questions)
            contexts = [
                ctxs if use else [] for ctxs, use in zip(contexts, use_contexts)
            ]

        # prepare system prompt
        prompts = []
        for question, ctxs in zip(questions, contexts):
            if len(ctxs) > 0:
                prompt = deepcopy(self.prompt_with_ctx)
            else:
                prompt = deepcopy(self.prompt_wo_ctx)

            # prepare user prompt
            usr_prompt = ""
            for n, context in enumerate(ctxs):
                if context.text:
                    ctx = context.text
                else:
                    ctx = context.full_text
                usr_prompt += f"Context {n + 1}: {ctx}\n\n"
            usr_prompt += f"Question: {question}"
            prompt.update(ChatTurn(role="user", content=usr_prompt))
            prompts.append(prompt)

        # generate response
        response = self.generator.chat(prompts, generation_config=self.gen_cfg)
        response = [i[0] for i in response]
        return response, prompts

    def decide_search(self, questions: list[str]) -> list[bool]:
        # TODO implement this
        raise NotImplementedError
