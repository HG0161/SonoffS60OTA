#!/usr/bin/env python3
"""Send an encrypted, read-only getState request to an owned eWeLink device."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import time
from pathlib import Path
from typing import Any

try:
    from tools.ewelink_crypto import decrypt_data, encrypt_data
except ModuleNotFoundError:  # Support direct execution as tools/lan_get_state.py.
    from ewelink_crypto import decrypt_data, encrypt_data


def private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
    finally:
        os.chmod(path, 0o600)


def get_state(
    ip: str,
    port: int,
    device: dict[str, Any],
    timeout: float,
    source_address: str | None = None,
) -> dict[str, Any]:
    encrypted, iv = encrypt_data({}, device["devicekey"])
    sequence = str(int(time.time() * 1000))
    payload = {
        "sequence": sequence,
        "deviceid": device["deviceid"],
        "selfApikey": "123",
        "data": encrypted,
        "encrypt": True,
        "iv": iv,
    }
    body = json.dumps(payload).encode("utf-8")
    source = (source_address, 0) if source_address else None
    connection = http.client.HTTPConnection(
        ip, port, timeout=timeout, source_address=source
    )
    try:
        connection.request(
            "POST",
            "/zeroconf/getState",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Connection": "close",
                "User-Agent": "s60-ota-research/0.1",
            },
        )
        response = connection.getresponse()
        raw = response.read(128 * 1024)
    finally:
        connection.close()
    result: dict[str, Any] = {
        "http_status": response.status,
        "http_reason": response.reason,
        "content_type": response.getheader("Content-Type"),
    }
    try:
        reply = json.loads(raw)
    except json.JSONDecodeError:
        result["body"] = raw.decode("utf-8", errors="replace")
        return result
    result["reply"] = reply
    if reply.get("data") and reply.get("iv"):
        result["decrypted"] = decrypt_data(reply["data"], reply["iv"], device["devicekey"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ip")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--source-address", help="local IPv4 address to bind")
    parser.add_argument("--key-file", type=Path, default=Path("captures/device-key.json"))
    parser.add_argument("--output", type=Path, default=Path("captures/lan-get-state.json"))
    args = parser.parse_args()

    device = json.loads(args.key_file.read_text(encoding="utf-8"))
    result = get_state(
        args.ip, args.port, device, args.timeout, args.source_address
    )
    private_json(args.output, result)
    print(f"HTTP status: {result['http_status']} {result['http_reason']}")
    if "reply" in result:
        print("eWeLink error:", result["reply"].get("error"))
    print(f"Saved private response to {args.output}")
    return 0 if result.get("http_status") == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
