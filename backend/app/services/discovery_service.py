"""mDNS printer discovery via python-zeroconf.

Papyrus runs as a single Docker container with ``network_mode: host`` so raw
mDNS multicast reaches the LAN, but the usual Linux mDNS tooling does not work
inside it: the container's Avahi daemon runs with ``enable-dbus=no`` and there
is no ``dbus-daemon``, so both ``avahi-browse`` and the CUPS ``dnssd`` backend
(which also talks to Avahi over D-Bus) are unusable. ``python-zeroconf``
implements the mDNS/DNS-SD protocol itself in pure Python/asyncio, so it needs
neither Avahi nor D-Bus and works as-is under ``network_mode: host``.

This module browses for the three service types printers advertise
(``_ipp._tcp``, ``_ipps._tcp``, ``_printer._tcp`` for IPP, IPPS, and LPD
respectively), listens for a fixed window, resolves whatever service names
were seen, and reduces the results to one entry per physical printer. It is a
leaf service with no knowledge of configured printers or HTTP routing — a
router calls :func:`discover_printers` and does its own enrichment
(``already_configured`` etc.).

Deployment is single-worker uvicorn, so there is no cross-process state here:
each call opens its own ``AsyncZeroconf``/``AsyncServiceBrowser`` pair and
tears it down before returning.

The zeroconf classes used below (``AsyncZeroconf``, ``AsyncServiceBrowser``,
``AsyncServiceInfo``) are imported at module level and referenced through the
module namespace (not aliased into closures) specifically so tests can
monkeypatch them with fakes and exercise this module without doing any real
network/mDNS I/O.
"""

import asyncio

from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

# Service types we browse for, and the protocol name each maps to in the
# returned records' ``protocols`` list / URI scheme choice.
_SERVICE_TYPE_IPP = "_ipp._tcp.local."
_SERVICE_TYPE_IPPS = "_ipps._tcp.local."
_SERVICE_TYPE_PRINTER = "_printer._tcp.local."
_SERVICE_TYPES = [_SERVICE_TYPE_IPP, _SERVICE_TYPE_IPPS, _SERVICE_TYPE_PRINTER]

_PROTOCOL_BY_SERVICE_TYPE = {
    _SERVICE_TYPE_IPP: "ipp",
    _SERVICE_TYPE_IPPS: "ipps",
    _SERVICE_TYPE_PRINTER: "lpd",
}

_DEFAULT_RESOURCE_PATH = "ipp/print"
_RESOLVE_TIMEOUT_MS = 2000


def _decode_txt(properties: dict) -> dict:
    """Decode a zeroconf TXT properties dict (``bytes`` keys, ``bytes | None``
    values) into ``str``/``None``, silently dropping anything undecodable."""
    decoded: dict = {}
    for raw_key, raw_value in properties.items():
        try:
            key = raw_key.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            continue
        if raw_value is None:
            decoded[key] = None
            continue
        try:
            decoded[key] = raw_value.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            decoded[key] = None
    return decoded


def _instance_name(name: str, service_type: str) -> str:
    """Strip the ``._ipp._tcp.local.``-style suffix off a full mDNS instance
    name, leaving just the human-readable printer name."""
    suffix = f".{service_type}"
    return name.removesuffix(suffix)


def _normalize_resource(resource_path: str | None) -> str:
    if not resource_path:
        return _DEFAULT_RESOURCE_PATH
    stripped = resource_path.strip("/")
    return stripped or _DEFAULT_RESOURCE_PATH


def _build_uri(protocol: str, ip: str, port: int, resource_path: str | None) -> str:
    if protocol == "lpd":
        return f"lpd://{ip}"
    scheme = "ipp" if protocol == "ipp" else "ipps"
    resource = _normalize_resource(resource_path)
    return f"{scheme}://{ip}:{port}/{resource}"


async def _resolve_one(zc, service_type: str, name: str) -> dict | None:
    """Resolve one browsed (service_type, name) pair into a device record, or
    ``None`` if it could not be resolved or has no usable IPv4 address."""
    try:
        info = AsyncServiceInfo(service_type, name)
        resolved = await info.async_request(zc, _RESOLVE_TIMEOUT_MS)
    except Exception:
        return None
    if not resolved:
        return None

    addresses = info.parsed_addresses(IPVersion.V4Only)
    if not addresses:
        return None
    ip = addresses[0]
    port = info.port
    props = _decode_txt(info.properties)

    protocol = _PROTOCOL_BY_SERVICE_TYPE[service_type]
    uri = _build_uri(protocol, ip, port, props.get("rp"))

    return {
        "name": _instance_name(name, service_type),
        "ip": ip,
        "port": port,
        "make_model": props.get("ty") or None,
        "location": props.get("note") or None,
        "uuid": props.get("UUID") or None,
        "protocol": protocol,
        "uri": uri,
    }


def _merge(records: list) -> list[dict]:
    """Dedupe resolved records into one entry per physical device.

    Dedupe key is the device's ``uuid`` when present, else its ``ip``.
    ``protocols`` accumulates every protocol seen for that key (in discovery
    order); the reported ``uri`` prefers ``ipp://`` over ``ipps://`` and only
    falls back to ``lpd://`` when neither IPP variant was seen.
    ``make_model``/``location`` take the first non-empty value seen for the
    key.
    """
    devices: dict = {}
    uris_by_key: dict = {}
    order: list = []

    for rec in records:
        if rec is None:
            continue
        key = rec["uuid"] or rec["ip"]
        if key not in devices:
            devices[key] = {
                "name": rec["name"],
                "ip": rec["ip"],
                "port": rec["port"],
                "make_model": rec["make_model"],
                "location": rec["location"],
                "uuid": rec["uuid"],
                "protocols": [],
            }
            uris_by_key[key] = {}
            order.append(key)

        device = devices[key]
        if rec["protocol"] not in device["protocols"]:
            device["protocols"].append(rec["protocol"])
        uris_by_key[key][rec["protocol"]] = rec["uri"]
        if not device["make_model"] and rec["make_model"]:
            device["make_model"] = rec["make_model"]
        if not device["location"] and rec["location"]:
            device["location"] = rec["location"]

    results = []
    for key in order:
        device = devices[key]
        protocol_uris = uris_by_key[key]
        device["uri"] = (
            protocol_uris.get("ipp") or protocol_uris.get("ipps") or protocol_uris.get("lpd")
        )
        results.append(device)

    return sorted(results, key=lambda d: d["name"])


async def discover_printers(timeout: float = 4.0) -> list[dict]:
    """Browse the LAN for printers advertising IPP/IPPS/LPD over mDNS.

    Listens for ``timeout`` seconds, then resolves every service name seen
    (concurrently, each with its own short timeout) and returns one entry per
    physical device: ``{name, ip, port, make_model, location, uri, uuid,
    protocols}``, sorted by ``name``. Devices with no resolvable IPv4 address
    are dropped. On a network with no mDNS responders this returns ``[]`` —
    it never raises.
    """
    seen: set = set()
    ordered: list = []

    def on_service_state_change(zeroconf, service_type, name, state_change):
        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            key = (service_type, name)
            if key not in seen:
                seen.add(key)
                ordered.append(key)

    aiozc = AsyncZeroconf()
    try:
        browser = AsyncServiceBrowser(
            aiozc.zeroconf, _SERVICE_TYPES, handlers=[on_service_state_change]
        )
        try:
            await asyncio.sleep(timeout)
        finally:
            await browser.async_cancel()

        records = await asyncio.gather(
            *(_resolve_one(aiozc.zeroconf, service_type, name) for service_type, name in ordered)
        )
    finally:
        await aiozc.async_close()

    return _merge(records)
