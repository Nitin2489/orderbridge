"""Tests for app/core/client_ip.py and TRUSTED_PROXY_CIDRS validation.

No database: these check which address rate limits are keyed on, and that a
client cannot choose it by sending X-Forwarded-For.
"""

import ipaddress

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.core.client_ip import UNKNOWN_CLIENT, client_ip, resolve_client_ip
from app.core.config import Settings

RENDER = [ipaddress.ip_network("10.0.0.0/8")]
TWO_HOPS = [ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("192.0.2.0/24")]


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "testing",
        "database_url": "postgresql+psycopg://unused:unused@localhost:5432/unused",
        "auth_secret_key": "client-ip-test-9f8e7d6c5b4a39281706f5e4d3c2b1a0",
        "auth_debug_otp": False,
        "auth_session_cookie_secure": False,
    }
    values.update(overrides)
    return Settings(**values)


# ---------------------------------------------------------------------------
# Untrusted peers: forwarded headers are ignored
# ---------------------------------------------------------------------------


def test_without_trusted_proxies_the_peer_is_the_client():
    assert resolve_client_ip("203.0.113.5", ["198.51.100.1"], []) == "203.0.113.5"


@pytest.mark.parametrize(
    "forged",
    [
        ["198.51.100.1"],
        ["198.51.100.1, 10.0.0.9"],
        ["10.0.0.9"],
        ["not-an-ip"],
        ["198.51.100.1", "198.51.100.2"],
    ],
)
def test_spoofed_header_from_an_untrusted_peer_is_ignored(forged):
    # Trusted proxies are configured, but this request did not come from one.
    assert resolve_client_ip("203.0.113.5", forged, RENDER) == "203.0.113.5"


# ---------------------------------------------------------------------------
# Trusted peers: walk X-Forwarded-For from the right
# ---------------------------------------------------------------------------


def test_trusted_proxy_supplies_the_client_address():
    assert resolve_client_ip("10.0.0.5", ["203.0.113.7"], RENDER) == "203.0.113.7"


@pytest.mark.parametrize(
    "spoofed_prefix", ["198.51.100.1", "1.1.1.1, 2.2.2.2", "10.0.0.1", "garbage"]
)
def test_client_supplied_entries_left_of_the_real_client_are_ignored(spoofed_prefix):
    header = f"{spoofed_prefix}, 203.0.113.7"

    assert resolve_client_ip("10.0.0.5", [header], RENDER) == "203.0.113.7"


def test_several_trusted_hops_are_skipped():
    # client -> 192.0.2.10 (trusted) -> 10.0.0.5 (trusted, the peer)
    header = "198.51.100.1, 203.0.113.7, 192.0.2.10"

    assert resolve_client_ip("10.0.0.5", [header], TWO_HOPS) == "203.0.113.7"


def test_repeated_headers_are_read_in_order():
    assert (
        resolve_client_ip("10.0.0.5", ["198.51.100.1", "203.0.113.7"], RENDER)
        == "203.0.113.7"
    )


def test_missing_header_from_a_trusted_proxy_uses_the_proxy():
    assert resolve_client_ip("10.0.0.5", [], RENDER) == "10.0.0.5"


def test_malformed_entry_stops_at_the_last_trusted_hop():
    assert resolve_client_ip("10.0.0.5", ["203.0.113.7, bogus"], RENDER) == "10.0.0.5"


def test_chain_of_only_trusted_hops_uses_the_leftmost():
    assert resolve_client_ip("10.0.0.5", ["10.1.1.1, 10.2.2.2"], RENDER) == "10.1.1.1"


def test_ipv6_and_ipv4_mapped_addresses_are_normalised():
    assert resolve_client_ip("::ffff:203.0.113.5", [], []) == "203.0.113.5"
    assert resolve_client_ip("2001:DB8::1", [], []) == "2001:db8::1"
    assert resolve_client_ip("10.0.0.5", ["2001:db8::1"], RENDER) == "2001:db8::1"


@pytest.mark.parametrize("peer", [None, "", "testclient", "300.1.1.1"])
def test_unparseable_peer_is_unknown(peer):
    assert resolve_client_ip(peer, ["203.0.113.7"], RENDER) == UNKNOWN_CLIENT


def test_x_real_ip_is_never_used():
    request = Request(
        {
            "type": "http",
            "client": ("203.0.113.5", 1234),
            "headers": [(b"x-real-ip", b"198.51.100.1")],
        }
    )

    assert client_ip(request, RENDER) == "203.0.113.5"


def test_client_ip_reads_the_request():
    request = Request(
        {
            "type": "http",
            "client": ("10.0.0.5", 1234),
            "headers": [(b"x-forwarded-for", b"198.51.100.1, 203.0.113.7")],
        }
    )

    assert client_ip(request, RENDER) == "203.0.113.7"
    assert client_ip(request, []) == "10.0.0.5"


# ---------------------------------------------------------------------------
# TRUSTED_PROXY_CIDRS validation
# ---------------------------------------------------------------------------


def test_trusted_proxies_default_to_none():
    assert make_settings().trusted_proxy_cidrs == []
    assert make_settings().trusted_proxy_networks == ()


def test_trusted_proxies_are_normalised_and_deduplicated():
    settings = make_settings(
        trusted_proxy_cidrs=["10.1.2.3/8", "10.0.0.0/8", "192.0.2.7", "2001:db8::/32"]
    )

    assert settings.trusted_proxy_cidrs == [
        "10.0.0.0/8",
        "192.0.2.7/32",
        "2001:db8::/32",
    ]


@pytest.mark.parametrize("cidr", ["not-a-network", "10.0.0.0/33", "", "10.0.0.0/8/1"])
def test_malformed_trusted_proxies_are_rejected(cidr):
    with pytest.raises(ValidationError, match="Invalid trusted proxy network"):
        make_settings(trusted_proxy_cidrs=[cidr])


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "::/0"])
def test_trusting_everyone_is_rejected(cidr):
    with pytest.raises(ValidationError, match="would trust every client"):
        make_settings(trusted_proxy_cidrs=[cidr])
