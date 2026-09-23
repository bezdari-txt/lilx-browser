"""Helpers for matching host names against user-defined site rules."""

from __future__ import annotations

from urllib.parse import urlsplit


def normalize_host(value: str) -> str:
    """Accept ``example.com``, ``https://www.example.com/path`` etc. and return a bare host.

    A leading ``www.`` is dropped so a rule for ``www.example.com`` covers the
    whole site. Returns an empty string for input that has no usable host.
    """
    value = value.strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    try:
        host = urlsplit(value).hostname or ""
    except ValueError:
        return ""
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or any(ch.isspace() for ch in host) or "." not in host and host != "localhost":
        return ""
    return host


def host_matches(host: str, rule_host: str) -> bool:
    """True if ``host`` is ``rule_host`` or one of its subdomains.

    Cookie domains may start with a dot (``.example.com``); it is ignored.
    """
    host = host.lower().lstrip(".")
    rule_host = rule_host.lower().lstrip(".")
    if not host or not rule_host:
        return False
    return host == rule_host or host.endswith("." + rule_host)
