#!/usr/bin/env python3
"""Discover eWeLink DNS-SD services using an unprivileged mDNS socket.

Background
----------
eWeLink (Sonoff) devices in "DIY mode" advertise themselves on the local
network with multicast DNS / DNS-SD: they publish a ``_ewelink._tcp.local``
service whose SRV record gives the device's port and whose TXT record carries
the device id, encryption flag and JSON state blob.

Rather than depending on a library such as ``zeroconf``, this script speaks
just enough of the DNS wire format (RFC 1035, plus the mDNS extensions in
RFC 6762) to send one PTR query and decode the replies. "Unprivileged" in the
docstring refers to the fallback in :func:`discover`: binding UDP port 5353 is
often impossible (another responder such as avahi/Bonjour already owns it, or
the process lacks the privilege), so the script falls back to an ephemeral
port and asks for unicast replies instead.
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Any

# The link-local multicast group and port reserved for mDNS (RFC 6762 §3).
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
# DNS-SD service type published by eWeLink/Sonoff devices in DIY mode.
SERVICE = "_ewelink._tcp.local"


def encode_name(name: str) -> bytes:
    """Encode a dotted DNS name into wire format.

    Each label is prefixed with its length byte and the whole name is
    terminated by a zero-length label, so ``"_ewelink._tcp.local"`` becomes
    ``b"\\x08_ewelink\\x04_tcp\\x05local\\x00"``. A trailing dot on the input is
    tolerated (stripped) so both ``"x.local"`` and ``"x.local."`` work.
    """
    return b"".join(bytes([len(part)]) + part.encode("utf-8") for part in name.rstrip(".").split(".")) + b"\0"


def read_name(packet: bytes, offset: int, seen: set[int] | None = None) -> tuple[str, int]:
    """Decode a (possibly compressed) DNS name starting at ``offset``.

    Returns ``(name, next_offset)`` where ``next_offset`` is the position in
    ``packet`` immediately after the encoded name — that is, where the caller
    should continue parsing, not where the name's data ended up.

    DNS name compression (RFC 1035 §4.1.4) lets a label sequence end with a
    two-byte pointer (top two bits set) to an earlier name in the same packet.
    When that happens the name continues elsewhere but the *caller's* cursor
    advances only past the two pointer bytes, which is what ``consumed``
    tracks. ``seen`` records pointer targets already followed so a malicious or
    corrupt packet pointing at itself raises instead of recursing forever.

    Raises ``ValueError`` on any truncated or malformed name.
    """
    labels: list[str] = []
    cursor = offset
    # Set only when the name terminated via a compression pointer; it is the
    # offset just past the two pointer bytes.
    consumed: int | None = None
    seen = set() if seen is None else seen
    while True:
        if cursor >= len(packet):
            raise ValueError("DNS name extends beyond packet")
        length = packet[cursor]
        if length == 0:
            # Root label: end of this name.
            cursor += 1
            break
        if length & 0xC0 == 0xC0:
            # Compression pointer: 14-bit offset spread over these two bytes.
            if cursor + 1 >= len(packet):
                raise ValueError("truncated DNS compression pointer")
            pointer = ((length & 0x3F) << 8) | packet[cursor + 1]
            if pointer in seen:
                raise ValueError("DNS compression pointer loop")
            seen.add(pointer)
            # Recurse to read the remainder of the name from the target, then
            # stop: a pointer is always the last thing in a name.
            pointed, _ = read_name(packet, pointer, seen)
            labels.append(pointed)
            consumed = cursor + 2
            cursor = consumed
            break
        # Ordinary label: `length` bytes of text follow the length byte.
        cursor += 1
        end = cursor + length
        if end > len(packet):
            raise ValueError("truncated DNS label")
        labels.append(packet[cursor:end].decode("utf-8", errors="replace"))
        cursor = end
    # Drop empty labels so a bare root name decodes to "" rather than ".".
    return ".".join(label for label in labels if label), consumed or cursor


def _decode_rdata(packet: bytes, rtype: int, offset: int, length: int) -> Any:
    """Decode one record's RDATA into a JSON-friendly Python value.

    ``packet`` (not just the RDATA slice) is passed in because PTR and SRV
    targets may use compression pointers into earlier parts of the packet.
    Record types this script does not care about fall through to a hex dump so
    nothing is silently lost.
    """
    raw = packet[offset : offset + length]
    if rtype in (12,):  # PTR — service instance name, e.g. "eWeLink_1000.…"
        return read_name(packet, offset)[0]
    if rtype == 33 and length >= 6:  # SRV — where the service actually lives
        priority, weight, port = struct.unpack_from("!HHH", packet, offset)
        target = read_name(packet, offset + 6)[0]
        return {"priority": priority, "weight": weight, "port": port, "target": target}
    if rtype == 16:  # TXT — one or more length-prefixed "key=value" strings
        values: list[str] = []
        cursor = 0
        while cursor < len(raw):
            size = raw[cursor]
            cursor += 1
            values.append(raw[cursor : cursor + size].decode("utf-8", errors="replace"))
            cursor += size
        return values
    if rtype == 1 and length == 4:  # A — IPv4 address of the device
        return socket.inet_ntop(socket.AF_INET, raw)
    if rtype == 28 and length == 16:  # AAAA — IPv6 address
        return socket.inet_ntop(socket.AF_INET6, raw)
    return raw.hex()


def parse_packet(packet: bytes) -> list[dict[str, Any]]:
    """Parse a DNS/mDNS message into a flat list of resource records.

    The 12-byte header gives the record counts for the four sections; the
    question section is skipped (its names are re-stated in the answers) and
    the answer, authority and additional sections are decoded. Each returned
    dict is JSON-serialisable so the caller can dump it straight to disk.

    Raises ``ValueError`` if the message is truncated at any point.
    """
    if len(packet) < 12:
        raise ValueError("truncated DNS header")
    # Header: id, flags, then the four section counts.
    _, _, questions, answers, authorities, additionals = struct.unpack_from("!HHHHHH", packet)
    cursor = 12
    for _ in range(questions):
        # Skip each question: its name, then QTYPE + QCLASS (4 bytes).
        _, cursor = read_name(packet, cursor)
        cursor += 4
        if cursor > len(packet):
            raise ValueError("truncated DNS question")
    records: list[dict[str, Any]] = []
    for section, count in (("answer", answers), ("authority", authorities), ("additional", additionals)):
        for _ in range(count):
            name, cursor = read_name(packet, cursor)
            # Fixed part of a resource record: TYPE, CLASS, TTL, RDLENGTH.
            if cursor + 10 > len(packet):
                raise ValueError("truncated DNS record")
            rtype, rclass, ttl, rdlength = struct.unpack_from("!HHIH", packet, cursor)
            cursor += 10
            if cursor + rdlength > len(packet):
                raise ValueError("truncated DNS record data")
            value = _decode_rdata(packet, rtype, cursor, rdlength)
            cursor += rdlength
            records.append(
                {
                    "section": section,
                    "name": name,
                    # Map the numeric type to a readable label, falling back to
                    # the number itself for anything unrecognised.
                    "type": {1: "A", 12: "PTR", 16: "TXT", 28: "AAAA", 33: "SRV"}.get(rtype, str(rtype)),
                    # In mDNS the top CLASS bit is the cache-flush flag, so mask
                    # it off to recover the real class (1 = IN) and expose the
                    # flag separately.
                    "class": rclass & 0x7FFF,
                    "cache_flush": bool(rclass & 0x8000),
                    "ttl": ttl,
                    "value": value,
                }
            )
    return records


def make_query(service: str = SERVICE) -> bytes:
    """Build the single-question mDNS query packet asking who offers ``service``."""
    # ID=0, standard mDNS query, one PTR question. Set the unicast-response bit
    # so discovery also works when binding multicast port 5353 is unavailable.
    #
    # Header fields: id=0, flags=0 (standard query), 1 question, no answers or
    # authority/additional records. The question is QTYPE=12 (PTR) and
    # QCLASS=0x8001 — class IN with the high "QU" bit set, which asks
    # responders to reply directly to our source port instead of multicasting.
    return struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + encode_name(service) + struct.pack("!HH", 12, 0x8001)


def discover(timeout: float, interface: str | None = None, target: str | None = None) -> dict[str, Any]:
    """Send one query and collect eWeLink-related replies for ``timeout`` seconds.

    ``interface`` is the local IPv4 address to send multicast from (useful on
    hosts with several NICs/VPNs, where the default route is the wrong one).
    ``target`` restricts the results to a single device IP.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bind_port = MDNS_PORT
    try:
        # Preferred path: bind 5353 so we can receive multicast replies too.
        sock.bind(("", MDNS_PORT))
    except OSError:
        # Port already owned by a system responder (avahi/Bonjour) or not
        # permitted — fall back to an ephemeral port. Only unicast replies
        # arrive in this mode, which is why make_query() sets the QU bit.
        bind_port = 0
        sock.bind(("", 0))
    interface_bytes = socket.inet_aton(interface or "0.0.0.0")
    if bind_port == MDNS_PORT:
        # Joining the group is only meaningful (and only permitted) when we
        # actually hold the mDNS port.
        membership = socket.inet_aton(MDNS_GROUP) + interface_bytes
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    if interface:
        # Pin outgoing multicast to the chosen interface.
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, interface_bytes)
    # Short per-recv timeout so the loop below can re-check the overall deadline
    # rather than blocking for the full duration on a quiet network.
    sock.settimeout(min(timeout, 0.5))
    sock.sendto(make_query(), (MDNS_GROUP, MDNS_PORT))
    packets: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload, source = sock.recvfrom(64 * 1024)
        except socket.timeout:
            # Nothing this interval; keep listening until the deadline.
            continue
        if target and source[0] != target:
            continue
        try:
            records = parse_packet(payload)
        except ValueError as exc:
            # Keep a note of unparseable traffic rather than dropping it, so a
            # failed discovery run is still diagnosable.
            packets.append({"source": source[0], "parse_error": str(exc)})
            continue
        # The socket also sees unrelated mDNS chatter (printers, AirPlay, …);
        # keep only packets that mention eWeLink anywhere in their decoded form.
        if any("ewelink" in json.dumps(record).lower() for record in records):
            packets.append({"source": source[0], "records": records})
    sock.close()
    return {
        "service": SERVICE,
        "target_filter": target,
        "interface": interface,
        "packets": packets,
    }


def main() -> int:
    """CLI entry point: run discovery and print or save the JSON result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--interface", help="local IPv4 address used for multicast")
    parser.add_argument("--target", help="only retain replies from this device IP")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(
        f"Listening for eWeLink devices for {args.timeout:g} seconds...",
        file=sys.stderr,
        flush=True,
    )
    result = discover(args.timeout, args.interface, args.target)
    # sort_keys keeps runs diffable against one another.
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        sources = sorted(
            {packet.get("source") for packet in result["packets"] if packet.get("source")}
        )
        if sources:
            print(f"Found eWeLink device(s): {', '.join(sources)}")
        else:
            print("No eWeLink devices replied.")
        print(f"Saved discovery report to {args.output}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
