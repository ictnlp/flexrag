import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from kylin.utils import Choices, LOGGER_MANAGER

from .index_base import DenseIndexBase, DenseIndexConfigBase, DENSE_INDEX

logger = LOGGER_MANAGER.get_logger("kylin.retriever.index.faiss")


@dataclass
class FaissIndexConfig(DenseIndexConfigBase):
    index_type: Choices(["FLAT", "IVF", "PQ", "IVFPQ", "auto"]) = "auto"  # type: ignore
    n_subquantizers: int = 8
    n_bits: int = 8
    n_list: int = 1000
    factory_str: Optional[str] = None
    # Inference Arguments
    n_probe: int = 32
    device_id: list[int] = field(default_factory=list)
    k_factor: int = 10
    polysemous_ht: int = 0
    efSearch: int = 100


@DENSE_INDEX("faiss", config_class=FaissIndexConfig)
class FaissIndex(DenseIndexBase):
    def __init__(self, cfg: FaissIndexConfig, index_path: str) -> None:
        super().__init__(cfg, index_path)
        # check faiss
        try:
            import faiss

            self.faiss = faiss
        except:
            raise ImportError(
                "Please install faiss by running `conda install -c pytorch -c nvidia faiss-gpu`"
            )

        # preapre inference args
        self.n_probe = cfg.n_probe
        self.device_id = (
            cfg.device_id if hasattr(self.faiss, "GpuMultipleClonerOptions") else []
        )
        self.k_factor = cfg.k_factor
        self.polysemous_ht = cfg.polysemous_ht
        self.efSearch = cfg.efSearch

        # prepare index args
        self.index_type = cfg.index_type
        self.distance_function = cfg.distance_function
        self.n_list = cfg.n_list
        self.n_subquantizers = cfg.n_subquantizers
        self.n_bits = cfg.n_bits
        self.factory_str = cfg.factory_str

        # prepare index
        if os.path.exists(self.index_path):
            self.index = self.deserialize()
        else:
            self.index = None
        return

    def build_index(self, embeddings: np.ndarray) -> None:
        self.clean()
        self.index = self._prepare_index(
            index_type=self.index_type,
            distance_function=self.distance_function,
            embedding_size=embeddings.shape[1],
            embedding_length=embeddings.shape[0],
            n_list=self.n_list,
            n_subquantizers=self.n_subquantizers,
            n_bits=self.n_bits,
            factory_str=self.factory_str,
        )
        self.train_index(embeddings=embeddings)
        self.add_embeddings(embeddings=embeddings)
        return

    def _prepare_index(
        self,
        index_type: str,
        distance_function: str,
        embedding_size: int,  # the dimension of the embeddings
        embedding_length: int,  # the number of the embeddings
        n_list: int,  # the number of cells
        n_subquantizers: int,  # the number of subquantizers
        n_bits: int,  # the number of bits per subquantizer
        factory_str: Optional[str] = None,
    ):
        # prepare distance function
        match distance_function:
            case "IP":
                basic_index = self.faiss.IndexFlatIP(embedding_size)
                basic_metric = self.faiss.METRIC_INNER_PRODUCT
            case "L2":
                basic_index = self.faiss.IndexFlatL2(embedding_size)
                basic_metric = self.faiss.METRIC_L2
            case _:
                raise ValueError(f"Unknown distance function: {distance_function}")

        if index_type == "auto":
            n_list = 2 ** int(np.log2(np.sqrt(embedding_length)))
            factory_str = f"IVF{n_list},PQ{embedding_size//2}x4fs"
            logger.info(f"Auto set index to {factory_str}")
            logger.info(
                f"We recommend to set n_probe to {n_list//8} for better inference performance"
            )

        if factory_str is not None:
            # using string factory to build the index
            index = self.faiss.index_factory(
                embedding_size,
                factory_str,
                basic_metric,
            )
        else:
            # prepare optimized index
            match index_type:
                case "FLAT":
                    index = basic_index
                case "IVF":
                    index = self.faiss.IndexIVFFlat(
                        basic_index,
                        embedding_size,
                        n_list,
                        basic_metric,
                    )
                case "PQ":
                    index = self.faiss.IndexPQ(
                        embedding_size,
                        n_subquantizers,
                        n_bits,
                    )
                case "IVFPQ":
                    index = self.faiss.IndexIVFPQ(
                        basic_index,
                        embedding_size,
                        n_list,
                        n_subquantizers,
                        n_bits,
                    )
                case _:
                    raise ValueError(f"Unknown index type: {index_type}")

        # post process
        index = self._set_index(index)
        return index

    def train_index(self, embeddings: np.ndarray) -> None:
        if self.is_flat:
            logger.info("Index is flat, no need to train")
            return
        logger.info("Training index")
        if (self.index_train_num >= embeddings.shape[0]) or (
            self.index_train_num == -1
        ):
            if embeddings.dtype != np.float32:
                embeddings = embeddings.astype("float32")
            self.index.train(embeddings)
        else:
            selected_indices = np.random.choice(
                embeddings.shape[0],
                self.index_train_num,
                replace=False,
            )
            selected_indices = np.sort(selected_indices)
            selected_embeddings = embeddings[selected_indices].astype("float32")
            self.index.train(selected_embeddings)
        return

    def _add_embeddings_batch(self, embeddings: np.ndarray) -> None:
        embeddings = embeddings.astype("float32")
        assert self.is_trained, "Index should be trained first"
        self.index.add(embeddings)  # debug
        return

    def prepare_search_params(self, **kwargs):
        # set search kwargs
        k_factor = kwargs.get("k_factor", self.k_factor)
        n_probe = kwargs.get("n_probe", self.n_probe)
        polysemous_ht = kwargs.get("polysemous_ht", self.polysemous_ht)
        efSearch = kwargs.get("efSearch", self.efSearch)

        def get_search_params(index):
            if isinstance(index, self.faiss.IndexRefine):
                params = self.faiss.IndexRefineSearchParameters(
                    k_factor=k_factor,
                    base_index_params=get_search_params(
                        self.faiss.downcast_index(index.base_index)
                    ),
                )
            elif isinstance(index, self.faiss.IndexPreTransform):
                params = self.faiss.SearchParametersPreTransform(
                    index_params=get_search_params(
                        self.faiss.downcast_index(index.index)
                    )
                )
            elif isinstance(index, self.faiss.IndexIVFPQ):
                if hasattr(index, "quantizer"):
                    params = self.faiss.IVFPQSearchParameters(
                        nprobe=n_probe,
                        polysemous_ht=polysemous_ht,
                        quantizer_params=get_search_params(
                            self.faiss.downcast_index(index.quantizer)
                        ),
                    )
                else:
                    params = self.faiss.IVFPQSearchParameters(
                        nprobe=n_probe, polysemous_ht=polysemous_ht
                    )
            elif isinstance(index, self.faiss.IndexIVF):
                if hasattr(index, "quantizer"):
                    params = self.faiss.SearchParametersIVF(
                        nprobe=n_probe,
                        quantizer_params=get_search_params(
                            self.faiss.downcast_index(index.quantizer)
                        ),
                    )
                else:
                    params = self.faiss.SearchParametersIVF(nprobe=n_probe)
            elif isinstance(index, self.faiss.IndexHNSW):
                params = self.faiss.SearchParametersHNSW(efSearch=efSearch)
            elif isinstance(index, self.faiss.IndexPQ):
                params = self.faiss.SearchParametersPQ(polysemous_ht=polysemous_ht)
            else:
                params = None
            return params

        return get_search_params(self.index)

    def _search_batch(
        self,
        query_vectors: np.ndarray,
        top_docs: int,
        **search_kwargs,
    ) -> tuple[np.ndarray, np.ndarray]:
        query_vectors = query_vectors.astype("float32")
        search_params = self.prepare_search_params(**search_kwargs)
        scores, indices = self.index.search(
            query_vectors, top_docs, params=search_params
        )
        return indices, scores

    def serialize(self) -> None:
        assert self.index.is_trained, "Index should be trained first."
        logger.info(f"Serializing index to {self.index_path}")
        if len(self.device_id) >= 0:
            cpu_index = self.faiss.index_gpu_to_cpu(self.index)
        else:
            cpu_index = self.index
        self.faiss.write_index(cpu_index, self.index_path)
        return

    def deserialize(self):
        logger.info(f"Loading index from {self.index_path}.")
        if (os.path.getsize(self.index_path) / (1024**3) > 10) and (
            len(self.device_id) == 0
        ):
            logger.info("Index file is too large. Loading on CPU with memory map.")
            cpu_index = self.faiss.read_index(self.index_path, self.faiss.IO_FLAG_MMAP)
        else:
            cpu_index = self.faiss.read_index(self.index_path)
        index = self._set_index(cpu_index)
        return index

    def clean(self):
        if self.index is None:
            return
        if os.path.exists(self.index_path):
            os.remove(self.index_path)
        self.index.reset()
        return

    @property
    def embedding_size(self) -> int:
        if self.index is None:
            raise ValueError("Index is not initialized.")
        return self.index.d

    @property
    def is_trained(self) -> bool:
        if self.index is None:
            return False
        if isinstance(self.index, self.faiss.IndexReplicas):
            trained = True
            for i in range(self.index.count()):
                sub_index = self.faiss.downcast_index(self.index.at(i))
                if not sub_index.is_trained:
                    trained = False
            return trained
        return self.index.is_trained

    @property
    def is_flat(self) -> bool:
        if self.index is None:
            return self.index_type == "FLAT"
        if isinstance(
            self.index,
            (self.faiss.IndexFlat, self.faiss.GpuIndexFlat),
        ):
            return True
        if isinstance(self.index, self.faiss.IndexReplicas):
            all_flat = True
            for i in range(self.index.count()):
                sub_index = self.faiss.downcast_index(self.index.at(i))
                if not isinstance(
                    sub_index,
                    (self.faiss.IndexFlat, self.faiss.GpuIndexFlat),
                ):
                    all_flat = False
            if all_flat:
                return True
        return False

    def __len__(self) -> int:
        if self.index is None:
            return 0
        return self.index.ntotal

    def _set_index(self, index):
        if len(self.device_id) > 0:
            logger.info("Accelerating index with GPU.")
            option = self.faiss.GpuMultipleClonerOptions()
            option.useFloat16 = True
            option.shard = True
            if isinstance(index, self.faiss.IndexIVFFlat):
                option.common_ivf_quantizer = True
            index = self.faiss.index_cpu_to_gpus_list(
                index,
                co=option,
                gpus=self.device_id,
                ngpu=len(self.device_id),
            )
        return index
