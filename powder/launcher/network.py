"""This PC's addresses in the local network: what friends type in to join a server hosted here."""
from __future__ import annotations

import ipaddress
import socket


def _usable(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and not (a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast)


def default_route_address() -> str | None:
    """The address Windows sends from towards the internet (the main network adapter). No packet is sent:
    connecting a UDP socket only picks the route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))          # documentation address: never reached
            ip = s.getsockname()[0]
    except OSError:
        return None
    return ip if _usable(ip) else None


def lan_addresses() -> list[str]:
    """IPv4 addresses of this PC (main adapter first), without loopback and self-assigned ones."""
    found = []
    first = default_route_address()
    if first:
        found.append(first)
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    for info in infos:
        ip = info[4][0]
        if _usable(ip) and ip not in found:
            found.append(ip)
    return found


def valid_address(text: str) -> bool:
    """An IPv4 address or a host name someone could type to join."""
    text = text.strip()
    if not text or " " in text:
        return False
    try:
        return ipaddress.ip_address(text).version == 4
    except ValueError:
        return all(part and (part.isalnum() or "-" in part) for part in text.split("."))
