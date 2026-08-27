"""Shared SSRF guard: resolve a hostname and reject addresses this
server has no legitimate reason to contact on an admin's behalf.

Private/LAN ranges are deliberately allowed -- printers, scanners, and
self-hosted WebDAV servers (Nextcloud, etc.) live there. Only
loopback/link-local/multicast/unspecified addresses are rejected, mirroring
`routers/printers.py`'s `_validate_probe_ip` but starting from a hostname
(which needs a DNS/getaddrinfo resolution step first) rather than a literal
IP.
"""
import asyncio
import ipaddress
import socket


class UnsafeHostError(ValueError):
    """Raised when a host is unresolvable, or resolves to an address this
    server must not contact on an admin's behalf."""


async def assert_safe_host(host: str) -> None:
    """Resolve `host` and raise `UnsafeHostError` if it's unresolvable or any
    resolved address is loopback/link-local/multicast/unspecified.

    Runs the blocking `getaddrinfo` call in a worker thread so it never
    stalls the event loop.
    """
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror as exc:
        raise UnsafeHostError(f"Could not resolve host: {host}") from exc

    for info in infos:
        raw_addr = info[4][0]
        # Strip an IPv6 zone id (e.g. "fe80::1%eth0") before parsing.
        addr = raw_addr.split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(addr)
        except ValueError:
            continue
        unsafe = (
            parsed.is_loopback or parsed.is_link_local
            or parsed.is_multicast or parsed.is_unspecified
        )
        if unsafe:
            raise UnsafeHostError(f"Refusing to contact restricted address: {addr}")
