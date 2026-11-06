"""Web origin canonicalisation, shared by configuration and the CSRF check.

Configured FRONTEND_ORIGINS and incoming Origin/Referer values go through
the same canonicalize_origin(), so a trusted origin matches only itself.

Canonical form: scheme://host[:port]
- scheme: http or https, lower case;
- host: a DNS hostname in lower case (ASCII; browsers send IDNs as
  punycode), an IPv4 address, or an IPv6 address in brackets in compressed
  form;
- port: omitted when it is the scheme's default (80 for http, 443 for
  https), otherwise 1-65535.

Rejected: other schemes, wildcards, user credentials, any path except a
single "/", query strings, fragments, malformed hosts and malformed ports.
"""

import ipaddress
import re
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}

_HOST_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_NUMERIC_HOST = re.compile(r"[0-9.]+")
_FORBIDDEN_CHARACTERS = frozenset("*\\%@?# \t\r\n")


class InvalidOriginError(ValueError):
    """The value is not an acceptable http(s) origin."""


def _canonical_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        if address.version == 6:
            return f"[{address.compressed}]"
        return str(address)

    if _NUMERIC_HOST.fullmatch(host):
        # Looks like an IPv4 address but is not a valid one (e.g. 1.2.3.999
        # or zero-padded octets).
        raise InvalidOriginError("Malformed IPv4 address.")

    if len(host) > 253 or not all(
        _HOST_LABEL.fullmatch(label) for label in host.split(".")
    ):
        raise InvalidOriginError("Malformed hostname.")

    return host


def canonicalize_origin(value: str) -> str:
    """Return the canonical form of an origin, or raise InvalidOriginError."""
    candidate = value.strip()

    if not candidate or _FORBIDDEN_CHARACTERS.intersection(candidate):
        raise InvalidOriginError("Origin contains forbidden characters.")

    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError:
        # Unbalanced IPv6 brackets, or a non-numeric/out-of-range port.
        raise InvalidOriginError("Malformed origin.") from None

    scheme = parts.scheme.lower()

    if scheme not in DEFAULT_PORTS:
        raise InvalidOriginError("Only http and https origins are allowed.")
    if parts.path not in ("", "/"):
        raise InvalidOriginError("Origins must not include a path.")
    if parts.netloc.endswith(":") or port == 0:
        raise InvalidOriginError("Malformed port.")
    if not parts.hostname:
        raise InvalidOriginError("Origin has no host.")
    if not parts.hostname.isascii():
        raise InvalidOriginError("Hostnames must be ASCII (punycode).")

    host = _canonical_host(parts.hostname.lower())
    port_suffix = "" if port in (None, DEFAULT_PORTS[scheme]) else f":{port}"

    return f"{scheme}://{host}{port_suffix}"


def origin_from_referer(referer: str) -> str:
    """The canonical origin of a Referer URL (its path and query are ignored)."""
    try:
        parts = urlsplit(referer.strip())
    except ValueError:
        raise InvalidOriginError("Malformed Referer.") from None

    if not parts.scheme or not parts.netloc:
        raise InvalidOriginError("Referer is not an absolute URL.")

    return canonicalize_origin(f"{parts.scheme}://{parts.netloc}")
