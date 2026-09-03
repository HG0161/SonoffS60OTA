#!/usr/bin/env python3
"""Send a non-flashing OTA URL probe to one owned S60 via eWeLink."""

from __future__ import annotations

import argparse
import getpass
import http.client
import http.server
import ipaddress
import json
import os
import random
import socket
import string
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from tools.get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device, target_id_from_mdns
    from tools.query_ota import ota_identity
    from tools.websocket_minimal import WebSocket, WebSocketError
except ModuleNotFoundError:
    from get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device, target_id_from_mdns
    from query_ota import ota_identity
    from websocket_minimal import WebSocket, WebSocketError


WS_DISPATCH = {
    "cn": "https://cn-dispa.coolkit.cn/dispatch/app",
    "as": "https://as-dispa.coolkit.cc/dispatch/app",
    "us": "https://us-dispa.coolkit.cc/dispatch/app",
    "eu": "https://eu-dispa.coolkit.cc/dispatch/app",
}
SAFETY_FLAG = "--i-understand-this-sends-an-upgrade-command"


class CaptureHandler(http.server.BaseHTTPRequestHandler):
    server_version = "S60Probe/0.1"

    def _reject_without_body(self) -> None:
        self.server.captures.append(
            {
                "time": time.time(),
                "client": self.client_address[0],
                "method": self.command,
                "path": urllib.parse.urlsplit(self.path).path,
                "headers": dict(self.headers.items()),
            }
        )
        # Critical safety property: serve no body and always reject the file.
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self) -> None:
        self._reject_without_body()

    def do_HEAD(self) -> None:
        self._reject_without_body()

    def do_POST(self) -> None:
        self._reject_without_body()

    def do_PUT(self) -> None:
        self._reject_without_body()

    def log_message(self, format: str, *args: Any) -> None:
        pass


class CaptureServer(http.server.ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int]):
        super().__init__(address, CaptureHandler)
        self.captures: list[dict[str, Any]] = []


def preflight_capture_server(server: CaptureServer, listen_ip: str) -> int:
    """Prove the local endpoint rejects downloads with an empty response."""
    port = int(server.server_address[1])
    connection = http.client.HTTPConnection(listen_ip, port, timeout=3)
    try:
        connection.request("GET", "/__preflight__.bin")
        response = connection.getresponse()
        body = response.read()
        if response.status != 404 or response.getheader("Content-Length") != "0" or body:
            raise OSError("capture-server safety preflight failed")
    finally:
        connection.close()
    server.captures.clear()
    return port


def private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
    finally:
        os.chmod(path, 0o600)


def nonce() -> str:
    return "".join(random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(8))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-ip", required=True, help="this computer's private LAN IPv4 address")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--email")
    parser.add_argument("--country-code", default="+44")
    parser.add_argument("--region", choices=sorted(API), default="eu")
    parser.add_argument("--mdns-capture", type=Path, default=Path("captures/mdns-1.1.1.json"))
    parser.add_argument("--output", type=Path, default=Path("captures/ota-command-probe.json"))
    parser.add_argument("--wait", type=float, default=30.0)
    parser.add_argument(SAFETY_FLAG, action="store_true")
    args = parser.parse_args()

    recovery_lock = Path(__file__).resolve().parents[1] / "RECOVERY_LOCK"
    if recovery_lock.exists():
        print(f"Refusing: recovery lock is active at {recovery_lock}", file=sys.stderr)
        return 2

    if not getattr(args, "i_understand_this_sends_an_upgrade_command"):
        print(f"Refusing to run without {SAFETY_FLAG}", file=sys.stderr)
        return 2
    address = ipaddress.ip_address(args.listen_ip)
    if address.version != 4 or not address.is_private:
        print("--listen-ip must be a private IPv4 LAN address", file=sys.stderr)
        return 2

    email = args.email or input("eWeLink email: ").strip()
    password = getpass.getpass("eWeLink password (not stored): ")
    server = CaptureServer((args.listen_ip, args.port))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ws: WebSocket | None = None
    cloud_responses: list[dict[str, Any]] = []
    sent = False
    sequence = str(int(time.time() * 1000))
    try:
        serve_port = preflight_capture_server(server, args.listen_ip)
        device_id = target_id_from_mdns(args.mdns_capture)
        region, session = login_session(email, password, args.country_code, args.region)
        device = select_device(get_devices(region, session["at"]), device_id)
        identity = ota_identity(device)
        dispatch = api_json(WS_DISPATCH[region], headers={"Authorization": "Bearer " + session["at"]})
        host, port = dispatch["domain"], int(dispatch["port"])
        ws = WebSocket.connect(host, port)
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
        base = f"http://{args.listen_ip}:{serve_port}"
        probe_files = [
            {"downloadUrl": base + "/probe-user1.bin", "digest": "0" * 64, "name": "user1.bin"},
            {"downloadUrl": base + "/probe-user2.bin", "digest": "0" * 64, "name": "user2.bin"},
        ]
        ws.send_json(
            {
                "action": "upgrade",
                "deviceid": device["deviceid"],
                "apikey": device["apikey"],
                "userAgent": "app",
                "sequence": sequence,
                "params": {"model": identity["model"], "version": "9.9.9", "binList": probe_files},
            }
        )
        sent = True
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline:
            try:
                cloud_responses.append(ws.receive_json(timeout=1.0))
            except socket.timeout:
                pass
            if server.captures:
                time.sleep(2.0)
                break
    except (EwelinkError, WebSocketError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    finally:
        password = ""
        if ws:
            ws.close()
        server.shutdown()
        server.server_close()
        private_json(
            args.output,
            {
                "upgrade_command_sent": sent,
                "firmware_bytes_served": 0,
                "sequence": sequence,
                "http_requests": server.captures,
                "cloud_responses": cloud_responses,
            },
        )
    print(f"Upgrade command sent: {sent}")
    print("Firmware bytes served: 0")
    print(f"Device HTTP requests captured: {len(server.captures)}")
    print(f"Saved private result to {args.output}")
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
