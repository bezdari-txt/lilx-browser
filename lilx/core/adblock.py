"""lilBlock filter engine: network rules in Adblock Plus / EasyList syntax.

Supported:
* ``||example.com^`` domain anchors, ``|`` start/end anchors, ``*`` wildcards, ``^`` separators;
* ``@@`` exception rules;
* options: resource types (``script``, ``image``, ``xhr``, ``~script``, …),
  ``third-party`` / ``~third-party`` (``3p`` / ``1p``), ``domain=a.com|~b.a.com``,
  ``match-case`` and ``important`` (accepted, ignored).

Rules with options that lilBlock cannot honour (``redirect``, ``csp``,
``removeparam``, ``$document``, …) are skipped rather than applied
incorrectly. Cosmetic rules (``##``) are ignored: blocking happens only at the
network level, through the QtWebEngine request interceptor.

Lookups are indexed: domain rules by host suffix, other rules by a literal
token that must appear in the URL, so large lists (EasyList has ~50k network
rules) stay fast.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from lilx.core.hosts import host_matches

log = logging.getLogger(__name__)

RESOURCE_TYPES = frozenset({
    "script", "image", "stylesheet", "object", "xmlhttprequest", "subdocument",
    "ping", "media", "font", "websocket", "other",
})
_TYPE_ALIASES = {"xhr": "xmlhttprequest", "css": "stylesheet", "frame": "subdocument", "object-subrequest": "object"}
_IGNORED_OPTIONS = {"match-case", "important", "~match-case"}
_THIRD_PARTY = {"third-party": True, "3p": True, "~third-party": False, "first-party": False, "1p": False,
                "~first-party": True}

_TOKEN_RE = re.compile(r"[a-z0-9%]+")
_PURE_HOST_RE = re.compile(r"^\|\|([a-z0-9.-]+)\^?\|?$")
_HOST_PREFIX_RE = re.compile(r"^\|\|([a-z0-9.-]+)(?=[/^:])")

# Second-level public suffixes common enough to matter for third-party detection.
_TWO_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.jp", "ne.jp", "or.jp",
    "com.br", "com.cn", "com.tr", "com.ua", "co.kr", "co.nz", "co.in", "co.za", "com.mx", "com.ar",
    "com.sg", "com.hk", "com.tw", "msk.ru", "spb.ru", "com.ru", "github.io", "blogspot.com",
}


def base_domain(host: str) -> str:
    """Approximate registrable domain (``a.b.example.co.uk`` -> ``example.co.uk``)."""
    parts = host.lower().strip(".").split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


@dataclass(frozen=True, slots=True)
class Request:
    url: str  # lower-cased
    host: str
    first_party_host: str
    resource_type: str  # one of RESOURCE_TYPES, or "document" for main frames

    @property
    def third_party(self) -> bool:
        if not self.first_party_host:
            return True
        return base_domain(self.host) != base_domain(self.first_party_host)

    @classmethod
    def create(cls, url: str, host: str, first_party_host: str, resource_type: str) -> Request:
        return cls(url.lower(), host.lower(), first_party_host.lower(), resource_type)


@dataclass(frozen=True, slots=True)
class Rule:
    text: str
    regex: re.Pattern[str] | None  # None: the host index alone decides
    types: frozenset[str] | None  # None: every type except "document"
    third_party: bool | None
    include_domains: tuple[str, ...]
    exclude_domains: tuple[str, ...]

    def applies(self, request: Request) -> bool:
        if self.types is None:
            if request.resource_type == "document":
                return False
        elif request.resource_type not in self.types:
            return False
        if self.third_party is not None and request.third_party != self.third_party:
            return False
        if self.include_domains and not any(host_matches(request.first_party_host, d) for d in self.include_domains):
            return False
        if self.exclude_domains and any(host_matches(request.first_party_host, d) for d in self.exclude_domains):
            return False
        return self.regex is None or self.regex.search(request.url) is not None


class _RuleIndex:
    def __init__(self) -> None:
        self.by_host: dict[str, list[Rule]] = {}
        self.by_token: dict[str, list[Rule]] = {}
        self.generic: list[Rule] = []

    def add(self, rule: Rule, host: str | None, token: str | None) -> None:
        if host:
            self.by_host.setdefault(host, []).append(rule)
        elif token:
            self.by_token.setdefault(token, []).append(rule)
        else:
            self.generic.append(rule)

    def match(self, request: Request, tokens: Iterable[str]) -> Rule | None:
        labels = request.host.split(".")
        for i in range(len(labels)):
            for rule in self.by_host.get(".".join(labels[i:]), ()):
                if rule.applies(request):
                    return rule
        for token in tokens:
            for rule in self.by_token.get(token, ()):
                if rule.applies(request):
                    return rule
        for rule in self.generic:
            if rule.applies(request):
                return rule
        return None


def _pattern_to_regex(pattern: str) -> str:
    regex = ""
    if pattern.startswith("||"):
        regex = r"^[a-z][a-z0-9+.-]*://(?:[^/?#]*\.)?"
        pattern = pattern[2:]
    elif pattern.startswith("|"):
        regex = "^"
        pattern = pattern[1:]
    end_anchor = pattern.endswith("|")
    if end_anchor:
        pattern = pattern[:-1]
    for ch in pattern:
        if ch == "*":
            regex += ".*"
        elif ch == "^":
            regex += r"(?:[^a-z0-9_.%-]|$)"
        else:
            regex += re.escape(ch)
    return regex + ("$" if end_anchor else "")


def _pick_token(pattern: str) -> str | None:
    """Longest literal run that is guaranteed to be a whole token in matching URLs."""
    body = pattern.lstrip("|")
    best: str | None = None
    for match in _TOKEN_RE.finditer(body):
        start, end = match.span()
        before = body[start - 1] if start else ("|" if pattern.startswith("|") else "*")
        after = body[end] if end < len(body) else ("|" if pattern.endswith("|") else "*")
        if before == "*" or after == "*":
            continue
        token = match.group()
        if len(token) >= 3 and (best is None or len(token) > len(best)):
            best = token
    return best


def parse_rule(line: str) -> tuple[Rule, bool, str | None, str | None] | None:
    """Parse one filter line into (rule, is_exception, index_host, index_token)."""
    line = line.strip()
    if not line or line.startswith(("!", "[")) or "##" in line or "#@#" in line or "#?#" in line or "#$#" in line:
        return None
    exception = line.startswith("@@")
    if exception:
        line = line[2:]

    pattern, options = line, ""
    if "$" in line and not line.startswith("/"):
        pattern, _, options = line.rpartition("$")
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
        return None  # raw regex rules are rare and expensive; skipped
    pattern = pattern.lower()

    types: set[str] = set()
    excluded_types: set[str] = set()
    third_party: bool | None = None
    include: list[str] = []
    exclude: list[str] = []
    for option in filter(None, options.lower().split(",")):
        name, _, value = option.partition("=")
        if name in _IGNORED_OPTIONS:
            continue
        if name in _THIRD_PARTY:
            third_party = _THIRD_PARTY[name]
        elif name == "domain":
            for domain in value.split("|"):
                (exclude if domain.startswith("~") else include).append(domain.lstrip("~"))
        else:
            negated = name.startswith("~")
            kind = _TYPE_ALIASES.get(name.lstrip("~"), name.lstrip("~"))
            if kind not in RESOURCE_TYPES:
                return None  # unsupported option: skip the whole rule
            (excluded_types if negated else types).add(kind)

    rule_types: frozenset[str] | None = None
    if types:
        rule_types = frozenset(types - excluded_types)
    elif excluded_types:
        rule_types = RESOURCE_TYPES - excluded_types

    if pattern in ("", "*", "|", "||"):
        if not include:
            return None  # would match everything
        pattern = "*"

    host_match = _PURE_HOST_RE.match(pattern)
    if host_match:
        host, regex = host_match.group(1), None
    else:
        prefix = _HOST_PREFIX_RE.match(pattern)
        host = prefix.group(1) if prefix else None
        try:
            regex = re.compile(_pattern_to_regex(pattern))
        except re.error:
            return None
    token = None if host else _pick_token(pattern)
    rule = Rule(line, regex, rule_types, third_party, tuple(include), tuple(exclude))
    return rule, exception, host, token


class FilterEngine:
    def __init__(self) -> None:
        self._block = _RuleIndex()
        self._allow = _RuleIndex()
        self.rule_count = 0
        self.sources: list[str] = []

    def load_text(self, text: str, source: str = "<text>") -> int:
        added = 0
        for line in text.splitlines():
            parsed = parse_rule(line)
            if parsed is None:
                continue
            rule, exception, host, token = parsed
            (self._allow if exception else self._block).add(rule, host, token)
            added += 1
        self.rule_count += added
        self.sources.append(source)
        return added

    def load_file(self, path: Path) -> int:
        try:
            count = self.load_text(path.read_text(encoding="utf-8", errors="replace"), path.name)
        except OSError as exc:
            log.error("Cannot read filter list %s: %s", path, exc)
            return 0
        log.info("lilBlock: %d rules from %s", count, path.name)
        return count

    def match(self, request: Request) -> Rule | None:
        """Return the blocking rule for the request, or ``None`` if it may load."""
        tokens = set(_TOKEN_RE.findall(request.url))
        rule = self._block.match(request, tokens)
        if rule is None or self._allow.match(request, tokens) is not None:
            return None
        return rule
