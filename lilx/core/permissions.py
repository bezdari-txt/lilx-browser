"""Site permission rules (camera, microphone, location, notifications, clipboard).

Pure logic, no Qt. A decision for one permission kind on one site is resolved as:

1. the global default is "block"  -> block (hard switch: sites are never asked)
2. a rule for this exact origin     -> allow / block
3. the global default              -> allow / ask

Rules are keyed by *origin* (scheme://host[:port]) and never inherited by
subdomains or shared between http and https, so a rule for https://example.com
does not apply to http://example.com or https://mail.example.com.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

KINDS = ("camera", "microphone", "geolocation", "notifications", "clipboard")
DEFAULT_DECISIONS = ("ask", "allow", "block")
SITE_DECISIONS = ("allow", "block")
DEFAULTS: dict[str, str] = {kind: "ask" for kind in KINDS}
_DEFAULT_PORTS = {"http": 80, "https": 443}

SiteRules = Mapping[str, Mapping[str, str]]


class PermissionRuleError(ValueError):
    """Invalid permission value (message is safe to show)."""


def normalize_origin(value: str) -> str:
    """``https://Example.COM:443/path?q`` -> ``https://example.com``. Only http(s) origins."""
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        raise PermissionRuleError(f"not a web address: {value!r}") from None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        raise PermissionRuleError(f"only http and https sites can have permissions: {value!r}")
    try:
        host = host.encode("idna").decode("ascii")  # пример.рф -> xn--e1afmkfd.xn--p1ai
    except UnicodeError:
        raise PermissionRuleError(f"invalid host name: {value!r}") from None
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    return f"{scheme}://{host}" if port in (None, _DEFAULT_PORTS[scheme]) else f"{scheme}://{host}:{port}"


def resolve(kind: str, origin: str, defaults: Mapping[str, str], site_rules: SiteRules) -> str:
    """Effective decision for one kind on one origin: "allow", "block" or "ask"."""
    default = defaults.get(kind, "ask")
    if default == "block":
        return "block"
    rule = site_rules.get(origin, {}).get(kind)
    if rule in SITE_DECISIONS:
        return rule
    return default if default in DEFAULT_DECISIONS else "ask"


def resolve_many(kinds: Iterable[str], origin: str, defaults: Mapping[str, str], site_rules: SiteRules) -> str:
    """Combined decision for a request needing several kinds (camera + microphone).

    Any block wins; everything allowed means allow; otherwise the user is asked.
    """
    decisions = {resolve(kind, origin, defaults, site_rules) for kind in kinds}
    if not decisions or "block" in decisions:
        return "block"
    return "allow" if decisions == {"allow"} else "ask"


def validate_default(kind: str, value: str) -> None:
    if kind not in KINDS:
        raise PermissionRuleError(f"unknown permission: {kind}")
    if value not in DEFAULT_DECISIONS:
        raise PermissionRuleError(f"{kind} must be ask, allow or block")


def validate_site_rule(kind: str, value: str) -> None:
    if kind not in KINDS:
        raise PermissionRuleError(f"unknown permission: {kind}")
    if value not in SITE_DECISIONS:
        raise PermissionRuleError(f"{kind} must be allow or block")


def clean_defaults(data: object) -> dict[str, str]:
    """Stored defaults -> complete, valid mapping (missing or broken entries fall back to ask)."""
    result = dict(DEFAULTS)
    if isinstance(data, Mapping):
        for kind, value in data.items():
            if kind in KINDS and value in DEFAULT_DECISIONS:
                result[kind] = value
    return result


def clean_site_rules(data: object) -> dict[str, dict[str, str]]:
    """Stored site rules -> valid mapping; unknown kinds, values and bad origins are dropped."""
    result: dict[str, dict[str, str]] = {}
    if not isinstance(data, Mapping):
        return result
    for origin, rules in data.items():
        if not isinstance(origin, str) or not isinstance(rules, Mapping):
            continue
        try:
            origin = normalize_origin(origin)
        except PermissionRuleError:
            continue
        cleaned = {k: v for k, v in rules.items() if k in KINDS and v in SITE_DECISIONS}
        if cleaned:
            result.setdefault(origin, {}).update(cleaned)
    return result
