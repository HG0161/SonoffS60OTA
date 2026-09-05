#!/usr/bin/env python3
"""Replay the S60's observed 24-byte Range request to an exact manifest URL."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from archive.tools.download_ota import DownloadError, ota_files
except ModuleNotFoundError:
    from download_ota import DownloadError, ota_files


def private_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
    finally:
        os.chmod(path, 0o600)


def request_header(item: dict[str, str], timeout: float = 10.0) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(item["downloadUrl"])
    if parsed.scheme != "http" or not parsed.hostname:
        raise DownloadError("Header replay currently requires the exact HTTP manifest URL")
    port = parsed.port or 80
    path = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
    try:
        # Match the four headers observed from the owned S60. Avoid urllib's
        # additional Accept-Encoding header so the request shape stays exact.
        connection.putrequest("GET", path, skip_accept_encoding=True)
        connection.putheader("Range", "bytes=0-23")
        connection.putheader("User-Agent", "itead-device")
        connection.putheader("connection", "Keep-Alive")
        connection.endheaders()
        response = connection.getresponse()
        body = response.read(25)
        return {
            "name": item["name"],
            "status": response.status,
            "reason": response.reason,
            "content_length": response.getheader("Content-Length"),
            "content_range": response.getheader("Content-Range"),
            "content_type": response.getheader("Content-Type"),
            "body": body,
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=Path("captures/ota-metadata-1.2.0.json"))
    parser.add_argument("--version", default="1.2.0")
    parser.add_argument("--name", default="user1.bin")
    parser.add_argument("--output", type=Path, default=Path("captures/vendor-1.2.0/user1.header24.bin"))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    version, files = ota_files(metadata, args.version)
    matches = [item for item in files if item["name"] == args.name]
    if len(matches) != 1:
        raise DownloadError(f"Expected one manifest file named {args.name}")

    result = request_header(matches[0], args.timeout)
    body = result.pop("body")
    saved = result["status"] == 206 and len(body) == 24
    if saved:
        private_bytes(args.output, body)
    result["bytes_read"] = len(body)
    result["saved"] = saved
    result["version"] = version

    print(json.dumps(result, indent=2, sort_keys=True))
    if saved:
        print(f"Saved exactly 24 bytes to {args.output}")
    else:
        print("No header was saved; the response was not an exact 24-byte partial response.")
    print("No device or upgrade command was sent.")
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
