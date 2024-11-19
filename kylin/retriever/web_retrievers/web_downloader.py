import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, make_dataclass, field
from typing import Any, Optional

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from httpx import Client
from tenacity import retry, stop_after_attempt, wait_fixed

from kylin.utils import Register, Choices


WEB_DOWNLOADERS = Register("web_downloader")


@dataclass
class BaseWebDownloaderConfig:
    allow_parallel: bool = True


class WebDownloader(ABC):
    def __init__(self, cfg: BaseWebDownloaderConfig) -> None:
        self.allow_parallel = cfg.allow_parallel
        return

    def download(self, urls: str | list[str]) -> Any:
        if isinstance(urls, str):
            urls = [urls]
        if self.allow_parallel:
            with ThreadPoolExecutor() as executor:
                results = list(executor.map(self.download_page, urls))
        else:
            results = [self.download_page(url) for url in urls]
        return results

    async def async_download(self, urls: str | list[str]) -> Any:
        if isinstance(urls, str):
            urls = [urls]
        results = await asyncio.gather(
            *[asyncio.to_thread(partial(self.download_page, url=url)) for url in urls]
        )
        return results

    @abstractmethod
    def download_page(self, url: str) -> Any:
        return


@dataclass
class SimpleWebDownloaderConfig:
    proxy: Optional[str] = None
    timeout: float = 3.0
    max_retries: int = 3
    retry_delay: float = 0.5
    skip_bad_response: bool = True
    headers: Optional[dict] = None


@WEB_DOWNLOADERS("simple", config_class=SimpleWebDownloaderConfig)
class SimpleWebDownloader:
    def __init__(self, cfg: SimpleWebDownloaderConfig) -> None:
        # setting httpx client
        self.client = Client(
            headers=cfg.headers,
            proxies=cfg.proxy,
            timeout=cfg.timeout,
        )

        # setting retry parameters
        self.skip_bad_response = cfg.skip_bad_response
        self.max_retries = cfg.max_retries
        self.retry_delay = cfg.retry_delay
        return

    def download(self, url: str) -> str:
        @retry(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_fixed(self.retry_delay),
            retry_error_callback=lambda _: None if self.skip_bad_response else None,
        )
        def download_page(url):
            response = self.client.get(url)
            response.raise_for_status()
            return response.text

        return download_page(url)


web_downloader_fields = [
    (
        "web_downloader_type",
        Choices(WEB_DOWNLOADERS.names),
        field(default=WEB_DOWNLOADERS.names[0]),
    )
]
web_downloader_fields += [
    (
        f"{WEB_DOWNLOADERS[name]['short_names'][0]}_config",
        WEB_DOWNLOADERS[name]["config_class"],
        field(default_factory=WEB_DOWNLOADERS[name]["config_class"]),
    )
    for name in WEB_DOWNLOADERS.mainnames
]
WebDownloaderConfig = make_dataclass("WebDownloaderConfig", web_downloader_fields)


def load_web_downloader(config: WebDownloaderConfig) -> WebDownloader:  # type: ignore
    config_name = (
        f"{WEB_DOWNLOADERS[config.web_downloader_type]['short_names'][0]}_config"
    )
    sub_config = getattr(config, config_name)
    return WEB_DOWNLOADERS[config.web_downloader_type]["item"](sub_config)
