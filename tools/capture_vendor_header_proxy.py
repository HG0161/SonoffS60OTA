#!/usr/bin/env python3
"""Capture at most the vendor image's first 24 bytes through a fail-closed relay."""

from __future__ import annotations

import argparse
import getpass
import os
import hashlib
import http.client
import http.server
import ipaddress
import json
import socket
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from tools.activate_vendor_ota_probe import EXPECTED_HOST, EXPECTED_PORT, validate_manifest_target
    from tools.download_ota import DownloadError, ota_files
    from tools.get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device, target_id_from_mdns
    from tools.probe_ota_command import WS_DISPATCH, nonce, private_json
    from tools.probe_vendor_header import private_bytes
    from tools.query_ota import ota_identity, query_ota
    from tools.websocket_minimal import WebSocket, WebSocketError
except ModuleNotFoundError:
    from activate_vendor_ota_probe import EXPECTED_HOST, EXPECTED_PORT, validate_manifest_target
    from download_ota import DownloadError, ota_files
    from get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device, target_id_from_mdns
    from probe_ota_command import WS_DISPATCH, nonce, private_json
    from probe_vendor_header import private_bytes
    from query_ota import ota_identity, query_ota
    from websocket_minimal import WebSocket, WebSocketError


SAFETY_FLAG = "--i-confirm-dnat-is-limited-to-the-s60"
EXPECTED_RANGE = "bytes=0-23"
UPSTREAM_RANGE = "bytes=0-1023"
EXPECTED_AGENT = "itead-device"
MAX_VALID_ATTEMPTS = 4


def manifest_path(item: dict[str, str]) -> str:
    parsed = urllib.parse.urlsplit(item["downloadUrl"])
    return urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))


def fetch_header24(host: str, port: int, path: str, timeout: float = 5.0) -> dict[str, Any]:
    """Request one range and never read a body unless headers promise exactly 24 bytes."""
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    body = b""
    try:
        connection.putrequest("GET", path, skip_accept_encoding=True)
        connection.putheader("Range", UPSTREAM_RANGE)
        connection.putheader("User-Agent", EXPECTED_AGENT)
        connection.putheader("connection", "Keep-Alive")
        connection.endheaders()
        response = connection.getresponse()
        length = response.getheader("Content-Length")
        content_range = response.getheader("Content-Range")
        if response.status == 206 and length == "1024" and content_range and content_range.startswith("bytes 0-1023/"):
            body = response.read(1024)
        return {
            "status": response.status,
            "reason": response.reason,
            "content_length": length,
            "content_range": content_range,
            "content_type": response.getheader("Content-Type"),
            "body": body,
        }
    finally:
        connection.close()


class StrictRelayHandler(http.server.BaseHTTPRequestHandler):
    server_version = "S60HeaderRelay/0.1"

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self) -> None:
        relay: StrictRelayServer = self.server  # type: ignore[assignment]
        record: dict[str, Any] = {
            "time": time.time(),
            "client": self.client_address[0],
            "method": self.command,
            "path": self.path,
            "range": self.headers.get("Range"),
            "user_agent": self.headers.get("User-Agent"),
            "host": self.headers.get("Host"),
            "forwarded": False,
            "firmware_bytes_returned_to_device": 0,
        }
        expected_host = f"{relay.upstream_host}:{relay.upstream_port}"
        checks = {
            "source": self.client_address[0] == relay.source_ip,
            "path": self.path.split("?")[0].rsplit("/", 1)[0] == relay.expected_path.rsplit("/", 1)[0],
            "host": self.headers.get("Host") == expected_host,
            "range": self.headers.get("Range") == EXPECTED_RANGE,
            "agent": self.headers.get("User-Agent") == EXPECTED_AGENT,
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            record["rejected"] = failed
            relay.records.append(record)
            self._empty(403)
            return

        with relay.attempt_lock:
            if relay.valid_attempts >= relay.max_valid_attempts or relay.captured.is_set():
                record["rejected"] = ["attempt-limit"]
                relay.records.append(record)
                self._empty(429)
                return
            relay.valid_attempts += 1

        try:
            upstream = relay.fetcher(relay.upstream_host, relay.upstream_port, self.path)
            body = upstream.pop("body")
            record["forwarded"] = True
            record["upstream"] = upstream
            record["firmware_bytes_read"] = len(body)
            if len(body) == 1024:
                private_bytes(relay.output, body)
                record["captured_sha256"] = hashlib.sha256(body).hexdigest()
                relay.captured.set()
        except (OSError, http.client.HTTPException) as exc:
            record["upstream_error"] = str(exc)
        relay.records.append(record)

        # Critical safety property: even the captured 24 bytes are never relayed
        # to the device. It receives an empty failure and cannot advance OTA.
        self._empty(502)

    def do_HEAD(self) -> None:
        self._empty(405)

    def do_POST(self) -> None:
        self._empty(405)

    def do_PUT(self) -> None:
        self._empty(405)

    def log_message(self, format: str, *args: Any) -> None:
        pass


class StrictRelayServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        source_ip: str,
        upstream_host: str,
        upstream_port: int,
        expected_path: str,
        output: Path,
        max_valid_attempts: int = MAX_VALID_ATTEMPTS,
        fetcher: Any = fetch_header24,
    ):
        super().__init__(address, StrictRelayHandler)
        self.source_ip = source_ip
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.expected_path = expected_path
        self.output = output
        self.max_valid_attempts = max_valid_attempts
        self.fetcher = fetcher
        self.valid_attempts = 0
        self.attempt_lock = threading.Lock()
        self.captured = threading.Event()
        self.records: list[dict[str, Any]] = []


def preflight_relay(server: StrictRelayServer, listen_ip: str) -> None:
    """Prove that a non-S60 source receives an empty rejection."""
    connection = http.client.HTTPConnection(listen_ip, int(server.server_address[1]), timeout=3)
    try:
        connection.request(
            "GET",
            server.expected_path,
            headers={"Host": f"{server.upstream_host}:{server.upstream_port}", "Range": EXPECTED_RANGE, "User-Agent": EXPECTED_AGENT},
        )
        response = connection.getresponse()
        body = response.read()
        if response.status != 403 or response.getheader("Content-Length") != "0" or body:
            raise OSError("relay safety preflight failed")
    finally:
        connection.close()
    server.records.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-ip", required=True)
    parser.add_argument("--listen-port", type=int, default=8088)
    parser.add_argument("--source-ip", default="192.168.1.96")
    parser.add_argument("--email")
    parser.add_argument("--country-code", default="+44")
    parser.add_argument("--region", choices=sorted(API), default="eu")
    parser.add_argument("--version", default="1.2.0")
    parser.add_argument("--expected-current-version", default="1.1.1")
    parser.add_argument("--mdns-capture", type=Path, default=Path("captures/mdns-1.1.1.json"))
    parser.add_argument("--metadata-output", type=Path, default=Path("captures/ota-metadata-1.2.0.json"))
    parser.add_argument("--output", type=Path, default=Path("captures/vendor-proxy-probe.json"))
    parser.add_argument("--header-output", type=Path, default=Path("captures/vendor-1.2.0/user1.header1k.bin"))
    parser.add_argument("--wait", type=float, default=30.0)
    parser.add_argument(SAFETY_FLAG, action="store_true")
    args = parser.parse_args()

    recovery_lock = Path(__file__).resolve().parents[1] / "RECOVERY_LOCK"
    if recovery_lock.exists():
        print(f"Refusing: recovery lock is active at {recovery_lock}", file=sys.stderr)
        return 2

    if not getattr(args, "i_confirm_dnat_is_limited_to_the_s60"):
        print(f"Refusing to run without {SAFETY_FLAG}", file=sys.stderr)
        return 2
    listen_address = ipaddress.ip_address(args.listen_ip)
    source_address = ipaddress.ip_address(args.source_ip)
    if not listen_address.is_private or not source_address.is_private or listen_address.version != 4 or source_address.version != 4:
        print("Listener and source must be private IPv4 addresses", file=sys.stderr)
        return 2
    if listen_address == source_address or not 1 <= args.listen_port <= 65535 or args.wait <= 0:
        print("Invalid listener/source combination, port, or wait", file=sys.stderr)
        return 2

    email = args.email or input("eWeLink email: ").strip()
    password = os.environ.get("EWELINK_PASSWORD") or getpass.getpass("eWeLink password (not stored): ")
    ws: WebSocket | None = None
    server: StrictRelayServer | None = None
    thread: threading.Thread | None = None
    sent = False
    version: str | None = None
    cloud_responses: list[dict[str, Any]] = []
    sequence = str(int(time.time() * 1000))
    error: str | None = None
    try:
        device_id = target_id_from_mdns(args.mdns_capture)
        region, session = login_session(email, password, args.country_code, args.region)
        device = select_device(get_devices(region, session["at"]), device_id)
        identity = ota_identity(device)
        if identity["version"] != args.expected_current_version:
            raise EwelinkError(f"Device reports {identity['version']}, expected {args.expected_current_version}; refusing")
        if device.get("online") is False:
            raise EwelinkError("Device is offline; refusing to send an upgrade command")

        response = query_ota(region, session["at"], identity)
        metadata = {"query": identity, "response": response}
        private_json(args.metadata_output, metadata)
        version, files = ota_files(metadata, args.version)
        validate_manifest_target(files, EXPECTED_HOST, EXPECTED_PORT)
        first = next((item for item in files if item["name"] == "user1.bin"), files[0])

        server = StrictRelayServer(
            (args.listen_ip, args.listen_port),
            source_ip=args.source_ip,
            upstream_host=EXPECTED_HOST,
            upstream_port=EXPECTED_PORT,
            expected_path=manifest_path(first),
            output=args.header_output,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        preflight_relay(server, args.listen_ip)

        dispatch = api_json(WS_DISPATCH[region], headers={"Authorization": "Bearer " + session["at"]})
        ws = WebSocket.connect(dispatch["domain"], int(dispatch["port"]))
        now = time.time()
        ws.send_json({
            "action": "userOnline", "at": session["at"], "apikey": session["user"]["apikey"],
            "appid": APP_ID, "nonce": nonce(), "ts": int(now), "userAgent": "app",
            "sequence": str(int(now * 1000)), "version": 8,
        })
        handshake = ws.receive_json(timeout=10)
        if handshake.get("error", 0) != 0:
            raise EwelinkError("Cloud WebSocket authentication failed")

        ws.send_json({
            "action": "upgrade", "deviceid": device["deviceid"], "apikey": device["apikey"],
            "userAgent": "app", "sequence": sequence,
            "params": {"model": identity["model"], "version": version, "binList": files},
        })
        sent = True
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline and not server.captured.is_set():
            try:
                cloud_responses.append(ws.receive_json(timeout=1.0))
            except socket.timeout:
                pass
    except (DownloadError, EwelinkError, WebSocketError, OSError, KeyError, json.JSONDecodeError) as exc:
        error = str(exc)
        print(f"Error: {exc}", file=sys.stderr)
    finally:
        password = ""
        if ws:
            ws.close()
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=3.0)
        private_json(args.output, {
            "upgrade_command_sent": sent,
            "dnat_attested": bool(getattr(args, "i_confirm_dnat_is_limited_to_the_s60")),
            "target": f"{EXPECTED_HOST}:{EXPECTED_PORT}",
            "version": version,
            "header_saved": bool(server and server.captured.is_set()),
            "valid_attempts": server.valid_attempts if server else 0,
            "http_requests": server.records if server else [],
            "cloud_responses": cloud_responses,
            "firmware_bytes_returned_to_device": 0,
            "error": error,
            "sequence": sequence,
        })

    captured = bool(server and server.captured.is_set())
    print(f"Upgrade command sent: {sent}")
    print(f"1024-byte header captured: {captured}")
    print("Firmware bytes returned to device: 0")
    print(f"Saved private result to {args.output}")
    return 0 if captured else 1


if __name__ == "__main__":
    raise SystemExit(main())
