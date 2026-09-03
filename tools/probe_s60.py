#!/usr/bin/env python3
"""Perform non-mutating HTTP capability probes against one owned S60."""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import time
from pathlib import Path
from typing import Any


DEFAULT_PORTS = (80, 8081)
INFO_BODY = b'{"deviceid":"","data":{}}'


def request(ip: str, port: int, method: str, path: str, body: bytes | None = None, timeout: float = 2.0) -> dict[str, Any]:
    headers = {"User-Agent": "s60-ota-research/0.1", "Connection": "close"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    started = time.monotonic()
    connection = http.client.HTTPConnection(ip, port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read(128 * 1024)
        text = payload.decode("utf-8", errors="replace")
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass
        return {
            "port": port,
            "method": method,
            "path": path,
            "status": response.status,
            "reason": response.reason,
            "headers": dict(response.getheaders()),
            "body": parsed if parsed is not None else text,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except (OSError, http.client.HTTPException) as exc:
        return {
            "port": port,
            "method": method,
            "path": path,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
    finally:
        connection.close()


def probe(ip: str, ports: tuple[int, ...], timeout: float) -> dict[str, Any]:
    # Resolve before requests both to fail early and to record the selected target.
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(ip, None, type=socket.SOCK_STREAM)})
    probes: list[dict[str, Any]] = []
    for port in ports:
        probes.append(request(ip, port, "GET", "/", timeout=timeout))
        probes.append(request(ip, port, "POST", "/zeroconf/info", INFO_BODY, timeout=timeout))
    return {
        "target": ip,
        "resolved_addresses": addresses,
        "ports": list(ports),
        "mutating_requests_sent": False,
        "probes": probes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", help="single S60 IP address or hostname")
    parser.add_argument("--ports", default="80,8081", help="comma-separated HTTP ports")
    parser.add_argument("--timeout", type=float, default=2.0, help="per-request timeout in seconds")
    parser.add_argument("--output", type=Path, help="write JSON result to this path")
    args = parser.parse_args()

    ports = tuple(int(value) for value in args.ports.split(",") if value.strip())
    result = probe(args.target, ports, args.timeout)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

