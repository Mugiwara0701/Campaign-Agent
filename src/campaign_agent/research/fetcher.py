"""Fetch one page as plain text. Pages are untrusted data and the network is not trusted either.

Safe by construction: only http(s), only hosts the domain policy allows, every redirect hop checked again (no
redirect off-policy), at most three hops, text content only, a byte cap while reading, a timeout, and the
resolved address must be public (no loopback, private or link-local targets).
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from campaign_agent.research.search_client import DomainPolicy

_TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
_MAX_HOPS = 3
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}

Resolver = Callable[[str], list[str]]


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class Page:
    url: str
    text: str


def resolve_addresses(host: str) -> list[str]:
    try:
        return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]
    except OSError as exc:
        raise FetchError("the host did not resolve") from exc


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


class PageFetcher:
    def __init__(
        self,
        domains: DomainPolicy | None = None,
        *,
        timeout_s: float = 10.0,
        max_bytes: int = 200_000,
        max_chars: int = 4000,
        client: httpx.Client | None = None,
        resolver: Resolver = resolve_addresses,
    ) -> None:
        self._domains = domains or DomainPolicy()
        self._max_bytes = max_bytes
        self._max_chars = max_chars
        self._resolver = resolver
        self._client = client or httpx.Client(
            timeout=timeout_s,
            follow_redirects=False,
            headers={"User-Agent": "CampaignAgent-concept-research/1.0"},
        )
        self._cache: dict[str, Page] = {}

    def _check(self, url: str) -> None:
        if not self._domains.allows(url):
            raise FetchError("the address is not allowed")
        host = urlsplit(url).hostname or ""
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            addresses = self._resolver(host)
        if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
            raise FetchError("the address is not public")

    def fetch(self, url: str) -> Page:
        if url in self._cache:
            return self._cache[url]
        current = url
        for _ in range(_MAX_HOPS + 1):
            self._check(current)
            try:
                with self._client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("a redirect without a target")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise FetchError(f"status {response.status_code}")
                    kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if kind not in _TEXT_TYPES:
                        raise FetchError("not a text page")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) >= self._max_bytes:
                            break
            except httpx.HTTPError as exc:
                raise FetchError(type(exc).__name__) from exc
            raw = bytes(body[: self._max_bytes]).decode("utf-8", errors="replace")
            text = html_to_text(raw) if kind != "text/plain" else " ".join(raw.split())
            if not text:
                raise FetchError("the page has no text")
            page = Page(url=url, text=text[: self._max_chars])
            self._cache[url] = page
            return page
        raise FetchError("too many redirects")
