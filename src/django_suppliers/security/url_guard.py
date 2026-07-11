# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""SSRF guard for external URL fetches (feeds + image downloads).

Blocks non-http(s) schemes, missing/un-resolvable hosts, and (when enabled)
internal IP ranges (RFC1918, loopback, link-local, reserved, multicast).

Set ``SUPPLIER_BLOCK_PRIVATE_HOSTS = False`` in Django settings to disable
the IP check (useful in tests; default True).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from django.conf import settings

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _is_internal_ip(host: str) -> bool:
    try:
        addr_info = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve host: {host}") from exc
    for _family, *_rest, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def assert_safe_url(url: str) -> None:
    """Raise ``ValueError`` if URL is unsafe for outbound fetch.

    Blocks: non-http(s), missing host, private/loopback/link-local IPs.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Disallowed URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("URL missing hostname")
    if getattr(settings, "SUPPLIER_BLOCK_PRIVATE_HOSTS", True) and _is_internal_ip(parsed.hostname):
        raise ValueError(f"Internal host blocked: {parsed.hostname}")
