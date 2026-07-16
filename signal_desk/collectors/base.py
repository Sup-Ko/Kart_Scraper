"""Base class and shared HTTP helper for collectors."""

from __future__ import annotations

from abc import ABC, abstractmethod

import requests

from ..config import Config
from ..models import Item

USER_AGENT = "SignalDesk/0.1 (personal use; +https://github.com/)"
TIMEOUT = 20


class Collector(ABC):
    name: str = "collector"

    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    def collect(self) -> list[Item]:
        """Fetch from the source and return items."""

    def _get(self, url: str, **kwargs) -> requests.Response:
        headers = {"User-Agent": USER_AGENT}
        headers.update(kwargs.pop("headers", {}))
        resp = requests.get(url, headers=headers, timeout=TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp
