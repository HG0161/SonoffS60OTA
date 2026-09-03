#!/usr/bin/env python3
"""Temporarily reopen the zero-byte OTA capture endpoint; sends no command."""

from __future__ import annotations

import argparse
import ipaddress
import time

try:
    from tools.probe_ota_command import CaptureServer
except ModuleNotFoundError:
    from probe_ota_command import CaptureServer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-ip", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--wait", type=float, default=300.0)
    args = parser.parse_args()

    address = ipaddress.ip_address(args.listen_ip)
    if address.version != 4 or not address.is_private:
        parser.error("--listen-ip must be a private IPv4 LAN address")
    if args.wait <= 0:
        parser.error("--wait must be positive")

    server = CaptureServer((args.listen_ip, args.port))
    server.timeout = 1.0
    seen = 0
    deadline = time.monotonic() + args.wait
    print(f"Zero-byte listener active on {args.listen_ip}:{args.port}", flush=True)
    try:
        while time.monotonic() < deadline:
            server.handle_request()
            while seen < len(server.captures):
                request = server.captures[seen]
                print(
                    f"Rejected {request['method']} {request['path']} "
                    f"from {request['client']} with zero bytes",
                    flush=True,
                )
                seen += 1
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print(f"Listener closed; requests rejected: {seen}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
