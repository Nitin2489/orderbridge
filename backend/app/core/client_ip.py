"""The client IP address used for rate limiting.

X-Forwarded-For can be written by anyone, so it is believed only when the
request reached this process from a configured trusted proxy
(TRUSTED_PROXY_CIDRS), and then only as far as the chain of trusted proxies
goes:

1. The TCP peer (request.client) is not a trusted proxy, or none is
   configured: the peer is the client. Forwarded headers are ignored.
2. The peer is a trusted proxy: walk X-Forwarded-For from right to left.
   Each proxy appends the address it received the request from, so entries
   are skipped while they are trusted proxies, and the first untrusted
   address is the client. Anything to its left was supplied by the client
   and is never used, so a spoofed header cannot choose the key.
3. A malformed entry stops the walk: the last address reached (a trusted
   proxy) is used, which is conservative (it shares that proxy's limit)
   rather than exploitable.

X-Real-IP, Forwarded and vendor headers are never read.

This assumes the trusted proxies append to X-Forwarded-For (the common
convention). Confirm it on each deployment before relying on per-IP limits:
send a request with a forged X-Forwarded-For and check which address the
backend resolves.

uvicorn applies its own proxy-header handling to request.client for peers in
FORWARDED_ALLOW_IPS (default: loopback only). Run production with
--no-proxy-headers so this module is the only place forwarded headers are
interpreted.
"""

import ipaddress
from collections.abc import Sequence

from fastapi import Request

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

UNKNOWN_CLIENT = "unknown"


def parse_ip(value: str | None) -> IPAddress | None:
    """A normalised IP address, or None. IPv4-mapped IPv6 becomes IPv4."""
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def is_trusted(address: IPAddress, trusted: Sequence[IPNetwork]) -> bool:
    return any(address in network for network in trusted)


def resolve_client_ip(
    peer: str | None,
    forwarded_for: Sequence[str],
    trusted: Sequence[IPNetwork],
) -> str:
    """The client address from the peer and X-Forwarded-For header values."""
    peer_ip = parse_ip(peer)
    if peer_ip is None:
        return UNKNOWN_CLIENT
    if not trusted or not is_trusted(peer_ip, trusted):
        return str(peer_ip)

    entries = [entry for value in forwarded_for for entry in value.split(",")]
    client = peer_ip
    for entry in reversed(entries):
        address = parse_ip(entry)
        if address is None:
            break
        client = address
        if not is_trusted(address, trusted):
            break
    return str(client)


def client_ip(request: Request, trusted: Sequence[IPNetwork]) -> str:
    peer = request.client.host if request.client else None
    return resolve_client_ip(peer, request.headers.getlist("x-forwarded-for"), trusted)
