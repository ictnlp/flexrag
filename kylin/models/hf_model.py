import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import torch
from omegaconf import MISSING
from torch.nn.parallel import DataParallel as DP
from transformers import (
    AutoConfig,
    AutoModel,
    AutoModelForCausalLM,
    AutoModelForMaskedLM,
    AutoModelForSeq2SeqLM,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    BertPreTrainedModel,
    BertModel,
    XLMRobertaModel,
    XLMRobertaPreTrainedModel,
)
from transformers import GenerationConfig as HFGenerationConfig
from transformers import PreTrainedModel, PreTrainedTokenizer
from transformers.dynamic_module_utils import get_class_from_dynamic_module

from kylin.prompt import ChatPrompt, load_template
from kylin.utils import Choices, TIME_METER, LOGGER_MANAGER

from .model_base import (
    EncoderBase,
    EncoderBaseConfig,
    ENCODERS,
    GenerationConfig,
    GeneratorBase,
    GeneratorBaseConfig,
    GENERATORS,
)
from .utils import guess_model_name

logger = LOGGER_MANAGER.get_logger("kylin.models.hf_model")


def get_colbert_model(
    base_model: str = "bert",
    output_dim: int = 128,
    model_path: str = None,
):
    """Code adapted from https://github.com/hotchpotch/JQaRA/blob/main/evaluator/reranker/colbert_reranker.py"""
    match base_model:
        case "bert":
            pretrained_class = BertPreTrainedModel
            model_class = BertModel
        case "xlm-roberta":
            pretrained_class = XLMRobertaPreTrainedModel
            model_class = XLMRobertaModel
        case "self_implemented":
            model_cfg = AutoConfig.from_pretrained(model_path, trust_remote_code=True)
            assert "AutoModel" in model_cfg.auto_map
            model_class_str = model_cfg.auto_map["AutoModel"]
            pretrained_class_str = model_class_str.replace("Model", "PreTrainedModel")
            model_class = get_class_from_dynamic_module(model_class_str, model_path)
            pretrained_class = get_class_from_dynamic_module(
                pretrained_class_str, model_path
            )
        case _:
            raise ValueError(f"Unsupported base model: {base_model}")

    class ColBERTModel(pretrained_class):
        def __init__(self, config):
            super().__init__(config)
            setattr(self, self.base_model_prefix, model_class(config))
            self.linear = torch.nn.Linear(config.hidden_size, output_dim, bias=False)
            self.init_weights()
            return

        def forward(
            self,
            input_ids=None,
            attention_mask=None,
            token_type_ids=None,
            position_ids=None,
            head_mask=None,
            inputs_embeds=None,
            encoder_hidden_states=None,
            encoder_attention_mask=None,
            output_attentions=None,
            output_hidden_states=None,
        ):
            outputs = getattr(self, self.base_model_prefix)(
                input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
                position_ids=position_ids,
                head_mask=head_mask,
                inputs_embeds=inputs_embeds,
                encoder_hidden_states=encoder_hidden_states,
                encoder_attention_mask=encoder_attention_mask,
                output_attentions=output_attentions,
                output_hidden_states=True,  # Always output hidden states
            )

            sequence_output = outputs[0]
            return self.linear(sequence_output)

    return ColBERTModel


def load_hf_model(
    model_path: str,
    tokenizer_path: Optional[str] = None,
    model_type: Optional[str] = None,
    device_id: list[int] = [],
    load_dtype: str = "auto",
    trust_remote_code: bool = False,
    pipeline_parallel: bool = False,
    is_training: bool = False,
    colbert_base_model: str = "bert",
    colbert_dim: int = 128,
    other_model_kwargs: dict = {},
    other_tokenizer_kwargs: dict = {},
) -> tuple[PreTrainedModel, PreTrainedTokenizer]:
    # prepare dtype
    load_in_4bit = False
    load_in_8bit = False
    match load_dtype:
        case "bfloat16":
            load_dtype = torch.bfloat16
        case "bf16":
            load_dtype = torch.bfloat16
        case "float32":
            load_dtype = torch.float32
        case "fp32":
            load_dtype = torch.float32
        case "float16":
            load_dtype = torch.float16
        case "fp16":
            load_dtype = torch.float16
        case "half":
            load_dtype = torch.float16
        case "8bit":
            load_dtype = None
            load_in_8bit = True
        case "4bit":
            load_dtype = None
            load_in_4bit = True
        case "auto":
            load_dtype = "auto"
        case _:
            raise ValueError(f"Unsupported load_dtype: {load_dtype}")

    # prepare device
    if pipeline_parallel:
        device_map = "auto"
    elif torch.cuda.is_available() and (len(device_id) > 0):
        device_map = device_id[0]
    else:
        device_map = None

    # load model
    match model_type:
        case "causal_lm":
            model_class = AutoModelForCausalLM
        case "seq2seq":
            model_class = AutoModelForSeq2SeqLM
        case "sequence_classification":
            model_class = AutoModelForSequenceClassification
        case "colbert":
            model_class = get_colbert_model(colbert_base_model, colbert_dim, model_path)
        case "masked_lm":
            model_class = AutoModelForMaskedLM
        case _:
            model_class = AutoModel
    model = model_class.from_pretrained(
        model_path,
        device_map=device_map,
        torch_dtype=load_dtype,
        load_in_4bit=load_in_4bit,
        load_in_8bit=load_in_8bit,
        trust_remote_code=trust_remote_code,
        **other_model_kwargs,
    )

    # patch: some model does not support `int` device_map
    if isinstance(device_map, int):
        model = model.to(torch.device(device_map))

    if not is_training:
        model.eval()

    # load tokenizer
    if tokenizer_path is not None:
        tokenizer_path = tokenizer_path
    else:
        tokenizer_path = model_path
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_path,
        trust_remote_code=trust_remote_code,
        **other_tokenizer_kwargs,
    )
    return model, tokenizer


@dataclass
class HFModelConfig:
    model_path: str = MISSING
    tokenizer_path: Optional[str] = None
    trust_remote_code: bool = False
    device_id: list[int] = field(default_factory=list)
    load_dtype: Choices(  # type: ignore
        [
            "bfloat16",
            "bf16",
            "float32",
            "fp32",
            "float16",
            "fp16",
            "half",
            "8bit",
            "4bit",
            "auto",
        ]
    ) = "auto"


@dataclass
class HFGeneratorConfig(GeneratorBaseConfig, HFModelConfig):
    pipeline_parallel: bool = False
    use_minference: bool = False


@GENERATORS("hf", config_class=HFGeneratorConfig)
class HFGenerator(GeneratorBase):
    model: PreTrainedModel

    def __init__(self, cfg: HFGeneratorConfig) -> None:
        # load model
        self.model, self.tokenizer = load_hf_model(
            model_path=cfg.model_path,
            tokenizer_path=cfg.tokenizer_path,
            model_type="causal_lm",
            device_id=cfg.device_id,
            load_dtype=cfg.load_dtype,
            trust_remote_code=cfg.trust_remote_code,
            pipeline_parallel=cfg.pipeline_parallel,
        )
        self._patch_model()

        # prepare prompt function
        model_name = guess_model_name(self.model.config)
        self.template = load_template(model_name=model_name, tokenizer=self.tokenizer)

        # load minference
        if cfg.use_minference:
            assert (
                not cfg.pipeline_parallel
            ), "Minference does not support pipeline parallel"
            from minference import MInference

            try:
                inf_patch = MInference("minference", model_name)
                self.model = inf_patch(self.model)
            except Exception as e:
                logger.warning(f"Unable to load minference: {e}")
        return

    @TIME_METER("hf_generate")
    @torch.no_grad()
    def generate(
        self,
        prefixes: list[str],
        generation_config: GenerationConfig = GenerationConfig(),
    ) -> list[list[str]]:
        bsz = len(prefixes)
        sample_num = generation_config.sample_num
        inputs = self.tokenizer(
            prefixes, return_tensors="pt", padding=True, truncation=True
        )
        inputs = inputs.to(self.model.device)

        # prepare generation config
        hf_gen_cfg = self._get_options(generation_config)
        if generation_config.eos_token_id is not None:
            inputs["eos_token_id"] = generation_config.eos_token_id
        else:
            inputs["eos_token_id"] = self.tokenizer.eos_token_id

        # generate
        outputs = self.model.generate(
            **inputs,
            generation_config=hf_gen_cfg,
        )

        # truncate the input tokens
        outputs = outputs.view(bsz, sample_num, -1)
        input_lengths = inputs["attention_mask"].sum(dim=1)
        responses = []
        for i in range(bsz):
            samples = [sample[input_lengths[i] :] for sample in outputs[i]]
            samples = [
                self.tokenizer.decode(sample, skip_special_tokens=True)
                for sample in samples
            ]
            responses.append(samples)
        return responses

    async def async_generate(
        self,
        prefixes: list[str],
        generation_config: GenerationConfig = GenerationConfig(),
    ) -> list[list[str]]:
        return await asyncio.to_thread(
            self.generate,
            prefixes=prefixes,
            generation_config=generation_config,
        )

    def chat(
        self,
        prompts: list[ChatPrompt],
        generation_config: GenerationConfig = GenerationConfig(),
    ) -> list[list[str]]:
        assert self.template is not None, "Chat function is disabled."
        prefixes = [self.template.render_to_text(prompt) for prompt in prompts]
        return self.generate(prefixes, generation_config)

    async def async_chat(
        self,
        prompts: list[ChatPrompt],
        generation_config: GenerationConfig = GenerationConfig(),
    ) -> list[list[str]]:
        return await asyncio.to_thread(
            self.chat,
            prompts=prompts,
            generation_config=generation_config,
        )

    def _get_options(self, generation_config: GenerationConfig) -> HFGenerationConfig:
        return HFGenerationConfig(
            do_sample=generation_config.do_sample,
            temperature=generation_config.temperature,
            max_new_tokens=generation_config.max_new_tokens,
            top_p=generation_config.top_p,
            top_k=generation_config.top_k,
            num_return_sequences=generation_config.sample_num,
        )

    def _patch_model(self) -> None:
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.add_special_tokens({"pad_token": "<pad>"})
            self.model.resize_token_embeddings(len(self.tokenizer))
        return


@dataclass
class HFEncoderConfig(EncoderBaseConfig, HFModelConfig):
    max_encode_length: int = 512
    encode_method: Choices(["cls", "mean"]) = "mean"  # type: ignore
    normalize: bool = False
    prompt: str = ""  # used in nomic-text-embedding
    task: str = ""  # used in jina-embedding


@ENCODERS("hf", config_class=HFEncoderConfig)
class HFEncoder(EncoderBase):
    def __init__(self, cfg: HFEncoderConfig):
        self.devices = cfg.device_id
        # load model
        self.model, self.tokenizer = load_hf_model(
            model_path=cfg.model_path,
            tokenizer_path=cfg.tokenizer_path,
            load_dtype=cfg.load_dtype,
            device_id=cfg.device_id,
            trust_remote_code=cfg.trust_remote_code,
        )
        if len(self.devices) > 1:
            if hasattr(self.model, "encode"):
                logger.warning("Data parallel does not support self implemented model.")
                self.dp_model = None
            else:
                self.dp_model = DP(self.model, device_ids=self.devices)
        else:
            self.dp_model = None

        # setup arguments
        self.max_encode_length = cfg.max_encode_length
        self.encode_method = cfg.encode_method
        self.normalize = cfg.normalize
        self.prompt = cfg.prompt
        self.task = cfg.task
        return

    def get_embedding(
        self, hidden: torch.Tensor, attn_mask: torch.Tensor
    ) -> np.ndarray:
        if self.encode_method == "mean":
            attn_mask = attn_mask.to(hidden.device)
            embeddings = hidden.masked_fill(~attn_mask[..., None].bool(), 0.0)
            embeddings = embeddings.sum(dim=1) / attn_mask.sum(dim=1)[..., None]
        elif self.encode_method == "cls":
            embeddings = hidden[:, 0]
        else:
            raise ValueError(f"Unsupported encode method: {self.encode_method}")
        if self.normalize:
            embeddings = torch.nn.functional.normalize(embeddings, dim=1)
        return embeddings.cpu().numpy()

    @TIME_METER("hf_encode")
    def encode(self, texts: list[str | list[str]]) -> np.ndarray:
        if hasattr(self.model, "encode"):  # for jina-embedding
            return self.model.encode(
                texts, task=self.task, max_length=self.max_encode_length
            )
        if self.prompt:
            texts = [f"{self.prompt}{i}" for i in texts]
        if (len(texts) >= len(self.devices) * 8) and (self.dp_model is not None):
            encoder = self.dp_model
        else:
            encoder = self.model
        return self._encode(texts, encoder)

    async def async_encode(self, texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(self.encode, texts)

    @torch.no_grad()
    def _encode(
        self, texts: list[str | list[str]], model: torch.nn.Module | DP
    ) -> np.ndarray:
        input_dict = self.tokenizer.batch_encode_plus(
            texts,
            return_tensors="pt",
            max_length=self.max_encode_length,
            padding=True,
            truncation=True,
        )
        if not isinstance(model, DP):
            input_dict = input_dict.to(model.device)
        mask = input_dict["attention_mask"]
        output = model(**input_dict).last_hidden_state
        embeddings = self.get_embedding(output, mask)
        return embeddings

    @property
    def embedding_size(self) -> int:
        return self.model.config.hidden_size
