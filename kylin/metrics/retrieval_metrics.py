from dataclasses import dataclass
from typing import Optional

from kylin.retriever import RetrievedContext
from kylin.utils import TIME_METER

from .metrics_base import MetricsBase, METRICS

try:
    from .lib_rel import get_contain_map

    has_librel = True
except:
    has_librel = False


def get_contain_map_py(evidences: list[str], retrieved: list[str]) -> list[list[bool]]:
    if has_librel:
        return get_contain_map(evidences, retrieved)
    contain_map: list[list[bool]] = []
    for ret in retrieved:
        contain_map.append([])
        for evd in evidences:
            contain_map[-1].append(evd in ret)
    return contain_map


@dataclass
class SuccessRateConfig:
    eval_field: Optional[str] = None


@METRICS("retrieval_success_rate", config_class=SuccessRateConfig)
class SuccessRate(MetricsBase):
    def __init__(self, cfg: SuccessRateConfig) -> None:
        self.eval_field = cfg.eval_field
        return

    @TIME_METER("metrics.retrieval_success_rate")
    def compute(
        self,
        golden_responses: list[list[str]] = None,
        retrieved_contexts: list[list[str | RetrievedContext]] = None,
        **kwargs,
    ) -> tuple[float, dict]:
        # compute relevance map
        success_map: list[bool] = []
        for golds, ctxs in zip(golden_responses, retrieved_contexts):
            if isinstance(ctxs[0], RetrievedContext):
                ctxs = [ctx.data[self.eval_field] for ctx in ctxs]
            rel_map = get_contain_map_py(golds, ctxs)
            is_success = any([any(j) for j in rel_map])
            success_map.append(is_success)
        score = sum(success_map) / len(success_map)
        return score, {"success_map": success_map}


@dataclass
class RetrievalRecallConfig:
    evaluate_field: Optional[str] = None


@METRICS("retrieval_recall", config_class=RetrievalRecallConfig)
class RetrievalRecall(MetricsBase):
    @TIME_METER("metrics.retrieval_recall")
    def compute(
        self,
        retrieved_contexts: list[list[str | RetrievedContext]] = None,
        golden_contexts: list[list[str]] = None,
        **kwargs,
    ) -> tuple[float, object]:
        raise NotImplementedError
