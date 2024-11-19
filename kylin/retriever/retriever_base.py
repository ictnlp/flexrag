import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np

from kylin.text_process import Pipeline, PipelineConfig
from kylin.utils import SimpleProgressLogger, TimeMeter, Register

from kylin.cache import PersistentCache, PersistentCacheConfig, LMDBBackendConfig


logger = logging.getLogger(__name__)


SEMANTIC_RETRIEVERS = Register("semantic_retriever")
SPARSE_RETRIEVERS = Register("sparse_retriever")
WEB_RETRIEVERS = Register("web_retriever")

RETRIEVAL_CACHE = PersistentCache(
    PersistentCacheConfig(
        backend="lmdb",
        maxsize=10000000,
        evict_order="LRU",
        lmdb_config=LMDBBackendConfig(
            db_path=os.environ.get(
                "RETRIEVAL_CACHE_PATH",
                os.path.join(
                    os.path.expanduser("~"), ".cache", "librarian", "cache.lmdb"
                ),
            )
        ),
    )
)


def batched_cache(func):
    def hashkey(*args, **kwargs):
        """Return a cache key for the specified hashable arguments."""
        return tuple(args), tuple(sorted(kwargs.items()))

    def wrapper(
        self,
        query: list[str],
        top_k: int = 10,
        disable_cache: bool = False,
        **search_kwargs,
    ):
        # check query
        if isinstance(query, str):
            query = [query]

        # direct search
        if disable_cache:
            return func(self, query, top_k, **search_kwargs)

        # search from cache
        keys = [
            hashkey(cfg=self.cfg, query=q, top_k=top_k, **search_kwargs) for q in query
        ]
        results = [RETRIEVAL_CACHE.get(k, None)[0] for k in keys]

        # search from database
        new_query = [q for q, r in zip(query, results) if r is None]
        new_indices = [n for n, r in enumerate(results) if r is None]
        if new_query:
            new_results = func(self, new_query, top_k, **search_kwargs)
            for n, r in zip(new_indices, new_results):
                results[n] = r
                RETRIEVAL_CACHE[keys[n]] = r, keys[n]
        assert all(r is not None for r in results)
        return results

    return wrapper


@dataclass
class RetrieverConfig:
    log_interval: int = 100


@dataclass
class LocalRetrieverConfig(RetrieverConfig):
    batch_size: int = 32
    query_preprocess_pipeline: PipelineConfig = field(default_factory=PipelineConfig)  # type: ignore


@dataclass
class RetrievedContext:
    retriever: str
    query: str
    data: dict
    source: Optional[str] = None
    score: float = 0.0

    def to_dict(self):
        return {
            "retriever": self.retriever,
            "query": self.query,
            "source": self.source,
            "score": self.score,
        }


class Retriever(ABC):
    def __init__(self, cfg: RetrieverConfig):
        self.cfg = cfg
        self.log_interval = cfg.log_interval
        return

    async def async_search(
        self,
        query: list[str],
        top_k: int = 10,
        **search_kwargs,
    ) -> list[list[RetrievedContext]]:
        return await asyncio.to_thread(
            self.search,
            query=query,
            top_k=top_k,
            **search_kwargs,
        )

    @abstractmethod
    def search(
        self,
        query: list[str],
        top_k: int = 10,
        **search_kwargs,
    ) -> list[list[RetrievedContext]]:
        """Search queries.

        Args:
            query (list[str]): Queries to search.
            top_k (int, optional): N documents to return. Defaults to 10.

        Returns:
            list[list[RetrievedContext]]: A batch of list that contains k RetrievedContext.
        """
        return

    def test_speed(
        self,
        sample_num: int = 10000,
        test_times: int = 10,
        top_k: int = 1,
        **search_kwargs,
    ) -> float:
        from nltk.corpus import brown

        total_times = []
        sents = [" ".join(i) for i in brown.sents()]
        for _ in range(test_times):
            query = [sents[i % len(sents)] for i in range(sample_num)]
            start_time = time.perf_counter()
            _ = self.search(query, top_k, disable_cache=True, **search_kwargs)
            end_time = time.perf_counter()
            total_times.append(end_time - start_time)
        avg_time = sum(total_times) / test_times
        std_time = np.std(total_times)
        logger.info(
            f"Retrieval {sample_num} items consume: {avg_time:.4f} ± {std_time:.4f} s"
        )
        return end_time - start_time

    def close(self):
        return


class LocalRetriever(Retriever):
    def __init__(self, cfg: LocalRetrieverConfig) -> None:
        super().__init__(cfg)
        # set args for process documents
        self.batch_size = cfg.batch_size
        self.query_preprocess_pipeline = Pipeline(cfg.query_preprocess_pipeline)
        return

    @abstractmethod
    def add_passages(self, passages: Iterable[dict[str, Any]]):
        """
        Add passages to the retriever database
        """
        return

    @abstractmethod
    def search_batch(
        self,
        query: list[str],
        top_k: int = 10,
        **search_kwargs,
    ) -> list[list[RetrievedContext]]:
        """Search queries using local retriever.

        Args:
            query (list[str]): Queries to search.
            top_k (int, optional): N documents to return. Defaults to 10.

        Returns:
            list[list[RetrievedContext]]: A batch of list that contains k RetrievedContext.
        """
        return

    @batched_cache
    def search(
        self,
        query: list[str] | str,
        top_k: int = 10,
        no_preprocess: bool = False,
        **search_kwargs,
    ) -> list[list[RetrievedContext]]:
        # search for documents
        query = [query] if isinstance(query, str) else query
        if not no_preprocess:
            query = [self.query_preprocess_pipeline(q) for q in query]
        final_results = []
        p_logger = SimpleProgressLogger(logger, len(query), self.log_interval)
        for idx in range(0, len(query), self.batch_size):
            p_logger.update(1, "Retrieving")
            batch = query[idx : idx + self.batch_size]
            results_ = self.search_batch(batch, top_k, **search_kwargs)
            final_results.extend(results_)
        return final_results

    @abstractmethod
    def close(self):
        return

    @abstractmethod
    def clean(self) -> None:
        return

    @abstractmethod
    def __len__(self):
        return

    def _clean_cache(self):
        if self._cache is not None:
            self._cache.clean()
        return

    @property
    @abstractmethod
    def fields(self) -> list[str]:
        return
