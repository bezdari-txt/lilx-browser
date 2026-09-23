"""Network routing modes.

Only ``DIRECT`` exists today. The optional Tor mode is planned; this module is
the single place where it will be wired in. A proper Tor mode needs more than a
SOCKS proxy, and the architecture keeps room for all of it:

* a separate off-the-record profile (``QWebEngineProfileBuilder.createOffTheRecordProfile``)
  so Tor and non-Tor cookies/caches never mix;
* proxying *all* engine traffic via ``QNetworkProxy.setApplicationProxy``
  (must be done before the first page loads), including DNS resolution;
* disabling WebRTC non-proxied UDP and other leak vectors.
"""

from __future__ import annotations

from enum import Enum


class NetworkMode(Enum):
    DIRECT = "direct"
    TOR = "tor"


def apply_network_mode(mode: NetworkMode) -> None:
    """Configure process-wide networking. Must run before the web engine loads any page."""
    if mode is NetworkMode.DIRECT:
        return  # use the system network configuration
    raise NotImplementedError("Tor mode is not implemented yet")
