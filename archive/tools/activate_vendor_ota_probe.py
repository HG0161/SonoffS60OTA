#!/usr/bin/env python3
"""Test command-time vendor URL activation behind a pre-verified router block."""

from __future__ import annotations

import argparse
import getpass
import json
import socket
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from archive.tools.download_ota import DownloadError, ota_files
    from tools.get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device, target_id_from_mdns
    from tools.probe_ota_command import WS_DISPATCH, nonce, private_json
    from archive.tools.probe_vendor_header import private_bytes, request_header
    from tools.query_ota import ota_identity, query_ota
    from tools.websocket_minimal import WebSocket, WebSocketError
except ModuleNotFoundError:
    from download_ota import DownloadError, ota_files
    from tools.get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device, target_id_from_mdns
    from tools.probe_ota_command import WS_DISPATCH, nonce, private_json
    from probe_vendor_header import private_bytes, request_header
    from tools.query_ota import ota_identity, query_ota
    from tools.websocket_minimal import WebSocket, WebSocketError


SAFETY_FLAG = "--i-confirm-the-router-block-was-tested"
EXPECTED_HOST = "52.57.99.135"
EXPECTED_PORT = 8088


def validate_manifest_target(files: list[dict[str, str]], host: str, port: int) -> None:
    """Require every real firmware URL to match the exact tested firewall target."""
    for item in files:
        parsed = urllib.parse.urlsplit(item["downloadUrl"])
        actual_port = parsed.port or (80 if parsed.scheme == "http" else 443)
        if parsed.scheme != "http" or parsed.hostname != host or actual_port != port:
            raise DownloadError(
                f"Manifest target for {item['name']} is not the tested {host}:{port}; refusing"
            )


def sanitized_probe(result: dict[str, Any], started: float) -> tuple[dict[str, Any], bytes]:
    body = result.pop("body")
    result["bytes_read"] = len(body)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result, body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email")
    parser.add_argument("--country-code", default="+44")
    parser.add_argument("--region", choices=sorted(API), default="eu")
    parser.add_argument("--version", default="1.2.0")
    parser.add_argument("--expected-current-version", default="1.1.1")
    parser.add_argument("--blocked-host", default=EXPECTED_HOST)
    parser.add_argument("--blocked-port", type=int, default=EXPECTED_PORT)
    parser.add_argument("--mdns-capture", type=Path, default=Path("captures/mdns-1.1.1.json"))
    parser.add_argument("--metadata-output", type=Path, default=Path("captures/ota-metadata-1.2.0.json"))
    parser.add_argument("--output", type=Path, default=Path("captures/vendor-activation-probe.json"))
    parser.add_argument("--header-output", type=Path, default=Path("captures/vendor-1.2.0/user1.header24.bin"))
    parser.add_argument("--probe-seconds", type=float, default=12.0)
    parser.add_argument(SAFETY_FLAG, action="store_true")
    args = parser.parse_args()

    recovery_lock = Path(__file__).resolve().parents[1] / "RECOVERY_LOCK"
    if recovery_lock.exists():
        print(f"Refusing: recovery lock is active at {recovery_lock}", file=sys.stderr)
        return 2

    if not getattr(args, "i_confirm_the_router_block_was_tested"):
        print(f"Refusing to run without {SAFETY_FLAG}", file=sys.stderr)
        return 2
    if (args.blocked_host, args.blocked_port) != (EXPECTED_HOST, EXPECTED_PORT):
        print("The blocked target must match the independently tested firewall target", file=sys.stderr)
        return 2

    email = args.email or input("eWeLink email: ").strip()
    password = getpass.getpass("eWeLink password (not stored): ")
    ws: WebSocket | None = None
    sent = False
    saved = False
    probe_attempts: list[dict[str, Any]] = []
    cloud_responses: list[dict[str, Any]] = []
    worker_error: list[str] = []
    sequence = str(int(time.time() * 1000))
    result_version: str | None = None
    try:
        device_id = target_id_from_mdns(args.mdns_capture)
        region, session = login_session(email, password, args.country_code, args.region)
        device = select_device(get_devices(region, session["at"]), device_id)
        identity = ota_identity(device)
        if identity["version"] != args.expected_current_version:
            raise EwelinkError(
                f"Device reports {identity['version']}, expected {args.expected_current_version}; refusing"
            )
        if device.get("online") is False:
            raise EwelinkError("Device is offline; refusing to send an upgrade command")

        response = query_ota(region, session["at"], identity)
        metadata = {"query": identity, "response": response}
        private_json(args.metadata_output, metadata)
        result_version, files = ota_files(metadata, args.version)
        validate_manifest_target(files, args.blocked_host, args.blocked_port)
        first = next((item for item in files if item["name"] == "user1.bin"), files[0])

        preflight_started = time.monotonic()
        preflight, preflight_body = sanitized_probe(request_header(first, timeout=5.0), preflight_started)
        probe_attempts.append({"phase": "before-command", **preflight})
        if preflight["status"] == 206 and len(preflight_body) == 24:
            private_bytes(args.header_output, preflight_body)
            saved = True
            print("Fresh manifest URL was already readable; no upgrade command was sent.")
            return 0
        if preflight["status"] != 400 or preflight_body:
            raise DownloadError("Unexpected pre-command server response; refusing to send upgrade command")

        dispatch = api_json(WS_DISPATCH[region], headers={"Authorization": "Bearer " + session["at"]})
        ws = WebSocket.connect(dispatch["domain"], int(dispatch["port"]))
        now = time.time()
        ws.send_json(
            {
                "action": "userOnline",
                "at": session["at"],
                "apikey": session["user"]["apikey"],
                "appid": APP_ID,
                "nonce": nonce(),
                "ts": int(now),
                "userAgent": "app",
                "sequence": str(int(now * 1000)),
                "version": 8,
            }
        )
        handshake = ws.receive_json(timeout=10)
        if handshake.get("error", 0) != 0:
            raise EwelinkError("Cloud WebSocket authentication failed")

        started = time.monotonic()
        stop = threading.Event()

        def replay_worker() -> None:
            nonlocal saved
            while not stop.is_set() and time.monotonic() - started < args.probe_seconds:
                try:
                    probe, body = sanitized_probe(request_header(first, timeout=3.0), started)
                    probe_attempts.append({"phase": "after-command", **probe})
                    if probe["status"] == 206 and len(body) == 24:
                        private_bytes(args.header_output, body)
                        saved = True
                        stop.set()
                        return
                except (OSError, DownloadError) as exc:
                    worker_error.append(str(exc))
                stop.wait(0.5)

        ws.send_json(
            {
                "action": "upgrade",
                "deviceid": device["deviceid"],
                "apikey": device["apikey"],
                "userAgent": "app",
                "sequence": sequence,
                "params": {
                    "model": identity["model"],
                    "version": result_version,
                    "binList": files,
                },
            }
        )
        sent = True
        worker = threading.Thread(target=replay_worker, daemon=True)
        worker.start()
        deadline = time.monotonic() + args.probe_seconds
        while time.monotonic() < deadline and not stop.is_set():
            try:
                cloud_responses.append(ws.receive_json(timeout=1.0))
            except socket.timeout:
                pass
        stop.set()
        worker.join(timeout=4.0)
    except (DownloadError, EwelinkError, WebSocketError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    finally:
        password = ""
        if ws:
            ws.close()
        private_json(
            args.output,
            {
                "upgrade_command_sent": sent,
                "router_block_attested": bool(getattr(args, "i_confirm_the_router_block_was_tested")),
                "blocked_target": f"{args.blocked_host}:{args.blocked_port}",
                "version": result_version,
                "header_saved": saved,
                "probe_attempts": probe_attempts,
                "cloud_responses": cloud_responses,
                "worker_errors": worker_error,
                "sequence": sequence,
            },
        )

    print(f"Upgrade command sent: {sent}")
    print(f"24-byte header captured: {saved}")
    print(f"Probe attempts: {len(probe_attempts)}")
    print(f"Saved private result to {args.output}")
    return 0 if sent or saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
