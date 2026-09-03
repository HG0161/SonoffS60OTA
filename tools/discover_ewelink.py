#!/usr/bin/env python3
"""Discover eWeLink DNS-SD services using an unprivileged mDNS socket."""

from __future__ import annotations

import argparse
import json
import socket
import struct
import time
from pathlib import Path
from typing import Any


MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SERVICE = "_ewelink._tcp.local"


def encode_name(name: str) -> bytes:
    return b"".join(bytes([len(part)]) + part.encode("utf-8") for part in name.rstrip(".").split(".")) + b"\0"


def read_name(packet: bytes, offset: int, seen: set[int] | None = None) -> tuple[str, int]:
    labels: list[str] = []
    cursor = offset
    consumed: int | None = None
    seen = set() if seen is None else seen
    while True:
        if cursor >= len(packet):
            raise ValueError("DNS name extends beyond packet")
        length = packet[cursor]
        if length == 0:
            cursor += 1
            break
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(packet):
                raise ValueError("truncated DNS compression pointer")
            pointer = ((length & 0x3F) << 8) | packet[cursor + 1]
            if pointer in seen:
                raise ValueError("DNS compression pointer loop")
            seen.add(pointer)
            pointed, _ = read_name(packet, pointer, seen)
            labels.append(pointed)
            consumed = cursor + 2
            cursor = consumed
            break
        cursor += 1
        end = cursor + length
        if end > len(packet):
            raise ValueError("truncated DNS label")
        labels.append(packet[cursor:end].decode("utf-8", errors="replace"))
        cursor = end
    return ".".join(label for label in labels if label), consumed or cursor


def _decode_rdata(packet: bytes, rtype: int, offset: int, length: int) -> Any:
    raw = packet[offset : offset + length]
    if rtype in (12,):  # PTR
        return read_name(packet, offset)[0]
    if rtype == 33 and length >= 6:  # SRV
        priority, weight, port = struct.unpack_from("!HHH", packet, offset)
        target = read_name(packet, offset + 6)[0]
        return {"priority": priority, "weight": weight, "port": port, "target": target}
    if rtype == 16:  # TXT
        values: list[str] = []
        cursor = 0
        while cursor < len(raw):
            size = raw[cursor]
            cursor += 1
            values.append(raw[cursor : cursor + size].decode("utf-8", errors="replace"))
            cursor += size
        return values
    if rtype == 1 and length == 4:  # A
        return socket.inet_ntop(socket.AF_INET, raw)
    if rtype == 28 and length == 16:  # AAAA
        return socket.inet_ntop(socket.AF_INET6, raw)
    return raw.hex()


def parse_packet(packet: bytes) -> list[dict[str, Any]]:
    if len(packet) < 12:
        raise ValueError("truncated DNS header")
    _, _, questions, answers, authorities, additionals = struct.unpack_from("!HHHHHH", packet)
    cursor = 12
    for _ in range(questions):
        _, cursor = read_name(packet, cursor)
        cursor += 4
        if cursor > len(packet):
            raise ValueError("truncated DNS question")

    records: list[dict[str, Any]] = []
    for section, count in (("answer", answers), ("authority", authorities), ("additional", additionals)):
        for _ in range(count):
            name, cursor = read_name(packet, cursor)
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
                    "type": {1: "A", 12: "PTR", 16: "TXT", 28: "AAAA", 33: "SRV"}.get(rtype, str(rtype)),
                    "class": rclass & 0x7FFF,
                    "cache_flush": bool(rclass & 0x8000),
                    "ttl": ttl,
                    "value": value,
                }
            )
    return records


def make_query(service: str = SERVICE) -> bytes:
    # ID=0, standard mDNS query, one PTR question. Set the unicast-response bit
    # so discovery also works when binding multicast port 5353 is unavailable.
    return struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + encode_name(service) + struct.pack("!HH", 12, 0x8001)


def discover(timeout: float, interface: str | None = None, target: str | None = None) -> dict[str, Any]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bind_port = MDNS_PORT
    try:
        sock.bind(("", MDNS_PORT))
    except OSError:
        bind_port = 0
        sock.bind(("", 0))
    interface_bytes = socket.inet_aton(interface or "0.0.0.0")
    if bind_port == MDNS_PORT:
        membership = socket.inet_aton(MDNS_GROUP) + interface_bytes
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    if interface:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, interface_bytes)
    sock.settimeout(min(timeout, 0.5))
    sock.sendto(make_query(), (MDNS_GROUP, MDNS_PORT))

    packets: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload, source = sock.recvfrom(64 * 1024)
        except socket.timeout:
            continue
        if target and source[0] != target:
            continue
        try:
            records = parse_packet(payload)
        except ValueError as exc:
            packets.append({"source": source[0], "parse_error": str(exc)})
            continue
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--interface", help="local IPv4 address used for multicast")
    parser.add_argument("--target", help="only retain replies from this device IP")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = discover(args.timeout, args.interface, args.target)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

