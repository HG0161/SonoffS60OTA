#!/usr/bin/env python3
"""
relay_full_vendor_ota.py — relay the complete vendor firmware to the S60
while saving a local copy for checksum analysis.

Unlike capture_vendor_header_proxy.py (which returns 502 to the device after
capturing 1024 bytes), this script relays every byte of the vendor response back
to the device so it can complete a real vendor OTA flash to 1.2.0.

Risk: the device WILL flash vendor 1.2.0 firmware.
The payoff: we capture the full binary and can determine the checksum algorithm
used in the 100-byte header's 8-byte field at offset 0x50.

Usage (from the project root, outside device_bash):
  python3 tools/relay_full_vendor_ota.py \
      --listen-ip 192.168.1.141 --email YOUR@EMAIL \
      --i-accept-vendor-flash
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import http.client
import http.server
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    from tools.get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device
    from tools.probe_ota_command import WS_DISPATCH, nonce
    from tools.query_ota import ota_identity, query_ota
    from archive.tools.download_ota import ota_files
    from archive.tools.probe_vendor_header import private_bytes
    from tools.websocket_minimal import WebSocket, WebSocketError
except ModuleNotFoundError:
    from tools.get_device_key import API, APP_ID, EwelinkError, api_json, get_devices, login_session, select_device
    from tools.probe_ota_command import WS_DISPATCH, nonce
    from tools.query_ota import ota_identity, query_ota
    from download_ota import ota_files
    from probe_vendor_header import private_bytes
    from tools.websocket_minimal import WebSocket, WebSocketError


UPSTREAM_HOST = "52.57.99.135"
UPSTREAM_PORT = 8088
DEVICE_IP = "192.168.1.96"


class FullRelayHandler(http.server.BaseHTTPRequestHandler):
    server_version = "VendorRelay/1.0"
    protocol_version = "HTTP/1.1"   # keep-alive so device reuses connection

    def log_message(self, fmt, *args):
        pass  # quiet by default; we print our own lines

    def _send_error_plain(self, code):
        body = b""
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        relay: FullRelayServer = self.server  # type: ignore[assignment]
        range_hdr = self.headers.get("Range", "")
        path = self.path
        print(f"  [device] GET {path[:80]}  Range: {range_hdr or '(full)'}")

        # Only accept connections from the device (may appear as router due to MASQUERADE)
        allowed_ips = {DEVICE_IP, "192.168.1.1"}
        if self.client_address[0] not in allowed_ips:
            print(f"  [rejected] unexpected source {self.client_address[0]}")
            self._send_error_plain(403)
            return

        # Forward to vendor
        try:
            conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=60)
            conn.putrequest("GET", path, skip_accept_encoding=True)
            conn.putheader("User-Agent", "itead-device")
            conn.putheader("connection", "Keep-Alive")
            if range_hdr:
                conn.putheader("Range", range_hdr)
            conn.endheaders()
            resp = conn.getresponse()
            body = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            print(f"  [upstream error] {exc}")
            self._send_error_plain(502)
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # Store chunk at the right offset
        if range_hdr.startswith("bytes="):
            try:
                lo_str, hi_str = range_hdr[6:].split("-")
                lo = int(lo_str)
                hi = int(hi_str) if hi_str else lo + len(body) - 1
            except ValueError:
                lo = 0
        else:
            lo = 0

        with relay.lock:
            end = lo + len(body)
            if end > len(relay.buf):
                relay.buf.extend(b"\x00" * (end - len(relay.buf)))
            relay.buf[lo:end] = body
            relay.bytes_captured = max(relay.bytes_captured, end)

        print(f"  [relay]  {resp.status}  {len(body):,}B  total captured: {relay.bytes_captured:,}")

        # Forward the vendor response verbatim
        self.send_response(resp.status)
        skip_headers = {"transfer-encoding", "connection", "keep-alive"}
        for name, value in resp.getheaders():
            if name.lower() not in skip_headers:
                self.send_header(name, value)
        # Ensure keep-alive so device can issue the next range request
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


class FullRelayServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, FullRelayHandler)
        self.lock = threading.Lock()
        self.buf = bytearray()
        self.bytes_captured = 0


def analyse_binary(data: bytes, output_dir: Path) -> None:
    """Print checksum analysis for the captured vendor binary."""
    total = len(data)
    if total < 100:
        print(f"  Only {total} bytes — too short to analyse")
        return

    header = data[:100]
    payload = data[100:]

    print(f"\n{'='*60}")
    print(f"CAPTURED BINARY ANALYSIS ({total:,} bytes)")
    print(f"{'='*60}")

    # Header fields
    print(f"  Magic:   {header[0]:02x} {header[1]:02x}")
    version = header[2:8].decode(errors="replace").rstrip("\x00")
    print(f"  Version: {version}")
    ts = struct.unpack_from("<I", header, 0x14)[0]
    print(f"  Timestamp: {ts} = {time.strftime('%Y-%m-%d', time.gmtime(ts))}")
    model = header[0x18:0x36].decode(errors="replace").rstrip("\x00")
    print(f"  Model:   {model}")
    size_field = struct.unpack_from(">I", header, 0x4C)[0]
    print(f"  Size field (0x4C): {size_field:,} (0x{size_field:08x})")
    checksum = header[0x50:0x58]
    target = checksum.hex()
    print(f"  Checksum (0x50): {target}")

    # ESP32 magic at offset 100
    if len(data) >= 101:
        print(f"  ESP app magic: {data[100]:02x} (expect e9)")

    print(f"\n  Payload size: {len(payload):,} bytes")

    # Digest comparisons
    sha_full = hashlib.sha256(data).hexdigest()
    sha_payload = hashlib.sha256(payload).hexdigest()
    sha_header80 = hashlib.sha256(header[:0x50]).hexdigest()

    print(f"\n  SHA-256(full file):      {sha_full}")
    print(f"  SHA-256(payload bytes 100+): {sha_payload}")
    print(f"  SHA-256(header[:80]):    {sha_header80}")

    vendor_digest = "43620d7645b9cfd188181b0ac0d3e86934193ebd3dcd0f6bb1bc3d6b5046f68c"
    print(f"\n  Vendor manifest digest:  {vendor_digest}")
    print(f"  Full-file match:  {sha_full == vendor_digest}")
    print(f"  Payload match:    {sha_payload == vendor_digest}")

    # Checksum algorithm search
    print(f"\n  Searching for checksum algorithm producing {target}:")
    candidates = [
        ("SHA-256(full file)[:8]", hashlib.sha256(data).digest()[:8].hex()),
        ("SHA-256(payload)[:8]", hashlib.sha256(payload).digest()[:8].hex()),
        ("SHA-256(header[:80])[:8]", hashlib.sha256(header[:0x50]).digest()[:8].hex()),
        ("SHA-512(payload)[:8]", hashlib.sha512(payload).digest()[:8].hex()),
        ("SHA-1(payload)[:8]", hashlib.sha1(payload).digest()[:8].hex()),
        ("MD5(payload)[:8]", hashlib.md5(payload).digest()[:8].hex()),
    ]
    found = False
    for label, val in candidates:
        match = "*** MATCH ***" if val == target else ""
        print(f"    {label}: {val}  {match}")
        if match:
            found = True
    if not found:
        print("    No match found — checksum is likely HMAC or proprietary.")

    # Save binary
    out = output_dir / "user1.full.bin"
    private_bytes(out, bytes(data))
    print(f"\n  Saved {total:,} bytes → {out}")
    sha_saved = hashlib.sha256(bytes(data)).hexdigest()
    print(f"  SHA-256: {sha_saved}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-ip", default="192.168.1.141")
    parser.add_argument("--listen-port", type=int, default=8088)
    parser.add_argument("--email")
    parser.add_argument("--country-code", default="+44")
    parser.add_argument("--region", default="eu")
    parser.add_argument("--wait", type=float, default=180.0,
                        help="Seconds to wait for device to complete download")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("captures/vendor-1.2.0"))
    parser.add_argument("--mdns-capture", type=Path,
                        default=Path("captures/mdns-1.1.1.json"))
    parser.add_argument("--metadata", type=Path,
                        default=Path("captures/ota-metadata-1.2.0.json"))
    parser.add_argument("--i-accept-vendor-flash", action="store_true",
                        help="Required: acknowledge the device will flash vendor 1.2.0")
    args = parser.parse_args()

    recovery_lock = Path(__file__).resolve().parents[1] / "RECOVERY_LOCK"
    if recovery_lock.exists():
        print(f"Refusing: recovery lock is active at {recovery_lock}", file=sys.stderr)
        return 2

    if not args.i_accept_vendor_flash:
        print("ERROR: pass --i-accept-vendor-flash to confirm the device will flash vendor 1.2.0")
        return 2

    email = args.email or input("eWeLink email: ").strip()
    password = os.environ.get("EWELINK_PASSWORD") or getpass.getpass("eWeLink password: ")

    # Load metadata for URL/version
    metadata = json.loads(args.metadata.read_text())
    version, files = ota_files(metadata, "1.2.0")
    print(f"Vendor version: {version}")
    for f in files:
        print(f"  {f['name']}: {f['downloadUrl']}")
        print(f"    digest (vendor): {f['digest']}")

    # Start relay server
    server = FullRelayServer((args.listen_ip, args.listen_port))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"\nRelay listening on {args.listen_ip}:{args.listen_port}")
    print(f"Upstream: {UPSTREAM_HOST}:{UPSTREAM_PORT}")
    print(f"Device will flash vendor 1.2.0 — this is intentional.\n")

    # eWeLink login
    try:
        region, session = login_session(email, password, args.country_code, args.region)
        password = ""
        devices = get_devices(region, session["at"])
        device = next(
            (d for d in devices if "S60" in d.get("extra", {}).get("model", "")
             or "S60" in d.get("productModel", "")),
            None
        )
        if device is None:
            device = devices[0]
            print(f"Warning: using first device: {device.get('name')} ({device['deviceid']})")
        else:
            print(f"Device: {device.get('name')} ({device['deviceid']})")

        if not device.get("online"):
            print("ERROR: Device offline.")
            server.shutdown()
            return 1

        dispatch = api_json(WS_DISPATCH[region], headers={"Authorization": "Bearer " + session["at"]})
        ws = WebSocket.connect(dispatch["domain"], int(dispatch["port"]))
        now = time.time()
        ws.send_json({
            "action": "userOnline", "at": session["at"], "apikey": session["user"]["apikey"],
            "appid": APP_ID, "nonce": nonce(), "ts": int(now), "userAgent": "app",
            "sequence": str(int(now * 1000)), "version": 8,
        })
        ws.receive_json(timeout=10)

        seq = str(int(time.time() * 1000))
        ws.send_json({
            "action": "upgrade",
            "deviceid": device["deviceid"],
            "apikey": device["apikey"],
            "userAgent": "app",
            "sequence": seq,
            "params": {"model": "SN-ESP32C3-S60-01", "version": version, "binList": files},
        })
        print(f"Upgrade command sent (seq {seq}). Waiting up to {args.wait:.0f}s...")

        expected_size = 1456404
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline:
            try:
                msg = ws.receive_json(timeout=2.0)
                print(f"  Cloud: {json.dumps(msg)}")
                if server.bytes_captured >= expected_size:
                    print(f"\n✓ Full {server.bytes_captured:,} bytes captured!")
                    break
            except socket.timeout:
                if server.bytes_captured > 0:
                    pct = server.bytes_captured / expected_size * 100
                    print(f"  Progress: {server.bytes_captured:,} / {expected_size:,} bytes ({pct:.1f}%)")
        ws.close()
    except (EwelinkError, WebSocketError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
    finally:
        password = ""
        server.shutdown()
        thread.join(timeout=3)

    if server.bytes_captured > 0:
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        analyse_binary(bytes(server.buf[:server.bytes_captured]), args.output_dir)
    else:
        print("No bytes captured — device did not connect.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
