"""Default-deny policy for servers that are not on this machine or a private network."""
from __future__ import annotations

import ipaddress
import socket


class HostPolicyError(RuntimeError):
    pass


def resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HostPolicyError(f"Could not resolve {host!r}: {exc.strerror or exc}") from exc
    return [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]


def is_private(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return addr.is_loopback or addr.is_private or addr.is_link_local


def check_host(host: str, allow_public: bool) -> None:
    """Raise HostPolicyError unless every address of `host` is local/private, or public is allowed."""
    addrs = resolve(host)
    if allow_public:
        return
    public = [str(a) for a in addrs if not is_private(a)]
    if public:
        raise HostPolicyError(
            f"{host} resolves to a public address ({public[0]}). PROMETHEUS only connects to "
            "localhost and private-network servers unless you enable public servers in Settings.")
