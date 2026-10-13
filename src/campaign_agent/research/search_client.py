"""Search through our own SearXNG instance. Results are untrusted data.

`DomainPolicy` is shared with the fetcher: a page is read only if its host is allowed. `SearchClient` is a protocol
so tests (and a later provider) plug in without the network.
"""

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

# Directories of people and companies, shops and social networks: never a source for concept research.
DEFAULT_DENY = (
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "pinterest.com",
    "amazon.com",
    "ebay.com",
    "alibaba.com",
    "aliexpress.com",
    "indeed.com",
    "glassdoor.com",
    "zoominfo.com",
    "crunchbase.com",
    "rocketreach.co",
    "apollo.io",
    "lusha.com",
    "yellowpages.com",
)


class SearchError(Exception):
    """The provider did not answer usefully. Research is fail-soft: the caller continues without it."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class DomainPolicy:
    allow: tuple[str, ...] = ()  # empty = any public host that is not denied
    deny: tuple[str, ...] = DEFAULT_DENY

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
            return False
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            return False
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return ip.is_global
        if _matches(host, self.deny):
            return False
        return not self.allow or _matches(host, self.allow)


def _matches(host: str, domains: Sequence[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in (x.lower().strip(".") for x in domains))


class SearchClient(Protocol):
    def search(self, query: str) -> list[SearchHit]: ...


class SearxngClient:
    def __init__(
        self,
        base_url: str,
        domains: DomainPolicy | None = None,
        *,
        timeout_s: float = 10.0,
        max_results: int = 5,
        max_bytes: int = 500_000,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/search"
        self._domains = domains or DomainPolicy()
        self._max_results = max_results
        self._max_bytes = max_bytes
        self._client = client or httpx.Client(timeout=timeout_s, follow_redirects=False)
        self._cache: dict[str, list[SearchHit]] = {}

    def search(self, query: str) -> list[SearchHit]:
        if query in self._cache:
            return self._cache[query]
        try:
            response = self._client.get(
                self._url, params={"q": query, "format": "json", "safesearch": "1"}
            )
            response.raise_for_status()
            if len(response.content) > self._max_bytes:
                raise SearchError("the search answer is too large")
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchError(f"search failed: {type(exc).__name__}") from exc
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in data.get("results", []) if isinstance(data, dict) else []:
            url = item.get("url") if isinstance(item, dict) else None
            if not isinstance(url, str) or url in seen or not self._domains.allows(url):
                continue
            seen.add(url)
            hits.append(
                SearchHit(
                    title=str(item.get("title", ""))[:200],
                    url=url[:500],
                    snippet=str(item.get("content", ""))[:500],
                )
            )
            if len(hits) >= self._max_results:
                break
        self._cache[query] = hits
        return hits
