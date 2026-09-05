#!/usr/bin/env python3
"""Serve a validated wrapped ESP32-C3 app to one owned S60.

This is the live, state-changing end of the research workflow.  It remains
blocked by ``RECOVERY_LOCK`` and also requires an explicit no-rollback
acknowledgement.  Building or validating an image never invokes this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import getpass
import http.server
import ipaddress
import json
import os
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from tools.analyze_vendor_ota import analyze
    from tools.build_vendor_ota import DEFAULT_MAX_PAYLOAD_SIZE, DEFAULT_MODEL
    from tools.get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device, target_id_from_mdns
    from tools.probe_ota_command import WS_DISPATCH, nonce
    from tools.query_ota import ota_identity
    from tools.websocket_minimal import WebSocket, WebSocketError
except ModuleNotFoundError:
    from analyze_vendor_ota import analyze
    from build_vendor_ota import DEFAULT_MAX_PAYLOAD_SIZE, DEFAULT_MODEL
    from get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device, target_id_from_mdns
    from probe_ota_command import WS_DISPATCH, nonce
    from query_ota import ota_identity
    from websocket_minimal import WebSocket, WebSocketError


SAFETY_FLAG = "--i-understand-stock-has-no-automatic-rollback"


class FirmwareHandler(http.server.BaseHTTPRequestHandler):
    """Serve the wrapped Tasmota binary, including Range request support."""
    server_version = "TasmotaOTA/1.0"
    protocol_version = "HTTP/1.1"  # keep-alive by default

    def log_message(self, fmt, *args):
        print(f"  [{self.client_address[0]}] {fmt % args}")

    def _empty_response(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        if self.client_address[0] != self.server.device_ip:
            self._empty_response(403)
            return
        path = urllib.parse.urlsplit(self.path).path
        if path not in {"/user1.bin", "/user2.bin"}:
            self._empty_response(404)
            return

        fw: bytes = self.server.firmware
        total = len(fw)

        range_hdr = self.headers.get("Range", "")
        if range_hdr:
            try:
                lo, hi = parse_single_range(range_hdr, total)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            chunk = fw[lo:hi + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {lo}-{hi}/{total}")
        else:
            lo, hi = 0, total - 1
            chunk = fw
            self.send_response(200)

        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(chunk)
        print(f"  Served {path} bytes {lo:,}-{hi:,} ({len(chunk):,} bytes)")
        self.server.bytes_served += len(chunk)
        self.server.served_ranges.append((lo, hi))


class FirmwareServer(http.server.HTTPServer):
    def __init__(self, addr, firmware: bytes, device_ip: str):
        super().__init__(addr, FirmwareHandler)
        self.firmware = firmware
        self.device_ip = device_ip
        self.bytes_served = 0
        self.served_ranges: list[tuple[int, int]] = []

    def unique_bytes_served(self) -> int:
        if not self.served_ranges:
            return 0
        merged = 0
        lo, hi = sorted(self.served_ranges)[0]
        for next_lo, next_hi in sorted(self.served_ranges)[1:]:
            if next_lo <= hi + 1:
                hi = max(hi, next_hi)
            else:
                merged += hi - lo + 1
                lo, hi = next_lo, next_hi
        return merged + hi - lo + 1


def parse_single_range(value: str, total: int) -> tuple[int, int]:
    """Parse the one explicit byte range supported by the stock updater."""
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("unsupported Range header")
    bounds = value[6:].split("-")
    if len(bounds) != 2 or not bounds[0]:
        raise ValueError("range start is required")
    try:
        lo = int(bounds[0])
        hi = int(bounds[1]) if bounds[1] else total - 1
    except ValueError as exc:
        raise ValueError("non-numeric Range header") from exc
    if lo < 0 or hi < lo or lo >= total or hi >= total:
        raise ValueError("range is outside the firmware body")
    return lo, hi


def cloud_summary(message: dict[str, Any]) -> dict[str, Any]:
    """Return diagnostic fields without printing device/account identifiers."""
    return {
        key: message[key]
        for key in ("action", "error", "sequence", "msg")
        if key in message
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-ip", required=True)
    parser.add_argument("--listen-port", type=int, default=8088)
    parser.add_argument("--device-ip", required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--country-code", default="+44")
    parser.add_argument("--region", default="eu")
    parser.add_argument("--expected-current-version", required=True)
    parser.add_argument("--mdns-capture", type=Path, required=True)
    parser.add_argument("--wait", type=float, default=120.0)
    parser.add_argument(SAFETY_FLAG, action="store_true")
    args = parser.parse_args()

    recovery_lock = Path(__file__).resolve().parents[1] / "RECOVERY_LOCK"
    if recovery_lock.exists():
        print(f"Refusing: recovery lock is active at {recovery_lock}", file=sys.stderr)
        return 2
    if not args.i_understand_stock_has_no_automatic_rollback:
        print(f"Refusing to run without {SAFETY_FLAG}", file=sys.stderr)
        return 2
    listen_address = ipaddress.ip_address(args.listen_ip)
    if listen_address.version != 4 or not listen_address.is_private:
        print("Refusing: --listen-ip must be a private IPv4 LAN address", file=sys.stderr)
        return 2
    device_address = ipaddress.ip_address(args.device_ip)
    if device_address.version != 4 or not device_address.is_private:
        print("Refusing: --device-ip must be a private IPv4 LAN address", file=sys.stderr)
        return 2

    try:
        firmware = args.firmware.read_bytes()
    except OSError as exc:
        print(f"Refusing: could not read firmware: {exc}", file=sys.stderr)
        return 2
    try:
        report = analyze(firmware)
    except ValueError as exc:
        print(f"Refusing invalid OTA file: {exc}", file=sys.stderr)
        return 2
    wrapper = report["wrapper"]
    esp = report["esp_image"]
    valid = (
        wrapper["magic_valid"] and wrapper["header_reserved_zero"]
        and wrapper["header_crc32_valid"] and wrapper["payload_offset_valid"]
        and wrapper["payload_size_valid"] and wrapper["payload_crc32_valid"]
        and wrapper["record_crc32_valid"] and wrapper["record_reserved_zero"]
        and wrapper["model"] == DEFAULT_MODEL
        and wrapper["actual_payload_size"] <= DEFAULT_MAX_PAYLOAD_SIZE
        and esp["chip_is_esp32_c3"] and esp["checksum_valid"]
        and esp["sha256_valid"] is not False and esp["trailing_bytes"] == 0
    )
    if not valid:
        print("Refusing: firmware failed the complete wrapper/native-image preflight", file=sys.stderr)
        return 2
    sha256_wrapped = hashlib.sha256(firmware).hexdigest()
    print(f"Firmware: {args.firmware} ({len(firmware):,} bytes)")

    password = os.environ.get("EWELINK_PASSWORD") or getpass.getpass("eWeLink password: ")
    server: FirmwareServer | None = None
    thread: threading.Thread | None = None
    ws: WebSocket | None = None
    sent = False
    run_error: str | None = None
    try:
        region, session = login_session(args.email, password, args.country_code, args.region)
        password = ""
        devices = get_devices(region, session["at"])
        device_id = target_id_from_mdns(args.mdns_capture)
        device = select_device(devices, device_id)
        identity = ota_identity(device)
        print(f"Device: {device.get('name')} ({identity['model']}, stock {identity['version']})")

        if not device.get("online"):
            raise EwelinkError("Device is offline; refusing to send")
        if identity["version"] != args.expected_current_version:
            raise EwelinkError(
                f"Device reports {identity['version']}, expected "
                f"{args.expected_current_version}; refusing"
            )

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

        server = FirmwareServer((args.listen_ip, args.listen_port), firmware, args.device_ip)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://{args.listen_ip}:{args.listen_port}"
        print(f"Serving only {args.device_ip} at {base_url}/user1.bin and /user2.bin")
        print(f"SHA-256 (complete wrapped file): {sha256_wrapped}")

        entries = [
            {"name": name, "downloadUrl": f"{base_url}/{name}", "digest": sha256_wrapped}
            for name in ("user1.bin", "user2.bin")
        ]
        seq = str(int(time.time() * 1000))
        ws.send_json({
            "action": "upgrade", "deviceid": device["deviceid"],
            "apikey": device["apikey"], "userAgent": "app", "sequence": seq,
            "params": {"model": identity["model"], "version": wrapper["header_version"], "binList": entries},
        })
        sent = True
        print("\nUpgrade command sent with owner-controlled private-LAN URLs.")
        print(f"Waiting up to {args.wait:.0f}s for device to connect...")

        deadline = time.monotonic() + args.wait
        complete_at: float | None = None
        import socket as _sock
        while time.monotonic() < deadline:
            # Delivery is checked first.  A quiet cloud socket must never delay
            # the exit: every byte may already be on the device.
            if server.unique_bytes_served() == len(firmware):
                if complete_at is None:
                    complete_at = time.monotonic()
                    print("  Complete firmware byte coverage observed; allowing verification time.")
                elif time.monotonic() - complete_at >= 8:
                    break

            if ws is not None:
                try:
                    msg = ws.receive_json(timeout=0.5)
                    print(f"  Cloud: {json.dumps(cloud_summary(msg))}")
                    if msg.get("sequence") == seq and msg.get("error", 0) != 0:
                        raise EwelinkError(f"Cloud rejected upgrade command: error {msg['error']}")
                except _sock.timeout:
                    pass
                except WebSocketError as exc:
                    # The device going offline for its reboot commonly closes
                    # the cloud stream. HTTP delivery must continue regardless.
                    print(f"  Cloud monitoring ended ({exc}); HTTP server remains active.")
                    try:
                        ws.close()
                    except OSError:
                        pass
                    ws = None

            else:
                time.sleep(0.25)
    except KeyboardInterrupt:
        # Ctrl-C after the whole image has been delivered is not a failure: the
        # device already has it and is rebooting.  Interrupting before that is.
        if server is not None and server.unique_bytes_served() == len(firmware):
            print("\n  Interrupted after every byte was delivered; treating as complete.")
        else:
            print("\n  Interrupted before the firmware was fully delivered.", file=sys.stderr)
            run_error = "interrupted before the firmware was fully delivered"
    except (EwelinkError, WebSocketError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        run_error = str(exc)
    finally:
        password = ""
        if ws is not None:
            ws.close()
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=3)

    if server is None:
        return 1
    unique = server.unique_bytes_served()
    print(f"\nTotal bytes served: {server.bytes_served:,}; unique firmware bytes: {unique:,}/{len(firmware):,}")
    if not sent or server.bytes_served == 0:
        print("Device did not connect; no firmware bytes were transferred.")
    elif unique != len(firmware):
        print("WARNING: the device did not request every firmware byte.")
    return 0 if run_error is None and sent and unique == len(firmware) else 1


if __name__ == "__main__":
    raise SystemExit(main())
