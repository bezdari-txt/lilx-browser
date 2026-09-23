"""Search engines and address-bar input resolution."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class SearchEngine:
    id: str
    name: str
    search_url: str  # contains "{query}"

    def url_for(self, query: str) -> str:
        return self.search_url.format(query=quote_plus(query))


ENGINES: dict[str, SearchEngine] = {
    engine.id: engine
    for engine in (
        SearchEngine("duckduckgo", "DuckDuckGo", "https://duckduckgo.com/?q={query}"),
        SearchEngine("google", "Google", "https://www.google.com/search?q={query}"),
        SearchEngine("bing", "Bing", "https://www.bing.com/search?q={query}"),
    )
}
DEFAULT_ENGINE = "duckduckgo"


def get_engine(engine_id: str) -> SearchEngine:
    return ENGINES.get(engine_id, ENGINES[DEFAULT_ENGINE])


# Schemes typed by users that should be opened as-is.
_DIRECT_SCHEMES = {"http", "https", "lilx", "file", "about", "view-source", "data"}
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9¡-￿]([a-zA-Z0-9¡-￿-]{0,61}[a-zA-Z0-9¡-￿])?\.)+"
    r"([a-zA-Z¡-￿]{2,63}|xn--[a-zA-Z0-9-]{2,59})$"
)


def _split_host(text: str) -> tuple[str, str]:
    """Split ``host[:port][/path...]`` into (host, port)."""
    authority = re.split(r"[/?#]", text, maxsplit=1)[0]
    if "@" in authority:  # user:pass@host is not something we open implicitly
        return "", ""
    if authority.startswith("["):  # [ipv6]:port
        end = authority.find("]")
        if end == -1:
            return "", ""
        return authority[1:end], authority[end + 2 :] if authority[end + 1 : end + 2] == ":" else ""
    host, _, port = authority.partition(":")
    return host, port


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def looks_like_url(text: str) -> bool:
    """Heuristic: does the user input look like an address rather than a search query?"""
    if not text or any(ch.isspace() for ch in text):
        return False
    host, port = _split_host(text)
    if not host or (port and not port.isdigit()):
        return False
    return host.lower() == "localhost" or _is_ip(host) or bool(_HOSTNAME_RE.match(host))


def resolve_input(text: str, engine_id: str) -> str:
    """Turn address-bar input into a URL: either the address itself or a search."""
    text = text.strip()
    if not text:
        return ""

    match = _SCHEME_RE.match(text)
    if match and match.group(1).lower() in _DIRECT_SCHEMES and not any(ch.isspace() for ch in text):
        return text

    if looks_like_url(text):
        host, _ = _split_host(text)
        # HTTPS-first for public hosts; local development hosts use plain HTTP.
        local = host.lower() == "localhost" or (_is_ip(host) and ipaddress.ip_address(host).is_private)
        return ("http://" if local else "https://") + text

    return get_engine(engine_id).url_for(text)
