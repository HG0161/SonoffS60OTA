#!/usr/bin/env python3
"""Fetch one owned device's LAN key without storing eWeLink credentials."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


# Public client credentials used by the open-source SonoffLAN integration at
# commit 3470f9133d4a3dcee520242f0da07114ad9d6bf4. These are application
# credentials, not user credentials. They may be rotated by eWeLink.
APP_ID = "R8Oq3y0eSZSYdKccHlrQzT1ACCOUT9Gv"
APP_SECRET = "1ve5Qk9GXfUhKAn1svnKwpAlxXkMarru"
API = {
    "cn": "https://cn-apia.coolkit.cn",
    "as": "https://as-apia.coolkit.cc",
    "us": "https://us-apia.coolkit.cc",
    "eu": "https://eu-apia.coolkit.cc",
}


class EwelinkError(RuntimeError):
    pass


def signed_login_body(email: str, password: str, country_code: str) -> tuple[bytes, dict[str, str]]:
    # Preserve insertion order and default json.dumps separators to match the
    # byte-for-byte request body covered by the HMAC signature.
    payload = {"password": password, "countryCode": country_code, "email": email}
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(APP_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    headers = {
        "Authorization": "Sign " + base64.b64encode(signature).decode("ascii"),
        "Content-Type": "application/json",
        "X-CK-Appid": APP_ID,
        "User-Agent": "s60-ota-research/0.1",
    }
    return body, headers


def api_json(url: str, *, body: bytes | None = None, headers: dict[str, str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers or {}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise EwelinkError(f"eWeLink HTTP error {exc.code}") from None
    except urllib.error.URLError as exc:
        raise EwelinkError(f"eWeLink connection failed: {exc.reason}") from None
    except json.JSONDecodeError:
        raise EwelinkError("eWeLink returned a non-JSON response") from None


def login_session(email: str, password: str, country_code: str, region: str) -> tuple[str, dict[str, Any]]:
    body, headers = signed_login_body(email, password, country_code)
    response = api_json(API[region] + "/v2/user/login", body=body, headers=headers)
    if response.get("error") == 10004:
        region = response.get("data", {}).get("region", "")
        if region not in API:
            raise EwelinkError("eWeLink redirected to an unknown region")
        response = api_json(API[region] + "/v2/user/login", body=body, headers=headers)
    if response.get("error") != 0:
        raise EwelinkError("eWeLink login failed: " + str(response.get("msg", "unknown error")))
    token = response.get("data", {}).get("at")
    if not token:
        raise EwelinkError("eWeLink login response did not contain an access token")
    return region, response["data"]


def login(email: str, password: str, country_code: str, region: str) -> tuple[str, str]:
    region, session = login_session(email, password, country_code, region)
    return region, session["at"]


def get_devices(region: str, token: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"num": 0})
    response = api_json(
        API[region] + "/v2/device/thing?" + query,
        headers={
            "Authorization": "Bearer " + token,
            "User-Agent": "s60-ota-research/0.1",
        },
    )
    if response.get("error") != 0:
        raise EwelinkError("Could not retrieve owned devices: " + str(response.get("msg", "unknown error")))
    things = response.get("data", {}).get("thingList", [])
    return [item["itemData"] for item in things if "deviceid" in item.get("itemData", {})]


def target_id_from_mdns(path: Path) -> str:
    capture = json.loads(path.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for packet in capture.get("packets", []):
        for record in packet.get("records", []):
            if record.get("type") != "TXT":
                continue
            for value in record.get("value", []):
                if value.startswith("id="):
                    ids.add(value[3:])
    if len(ids) != 1:
        raise EwelinkError(f"Expected one device ID in mDNS capture, found {len(ids)}")
    return ids.pop()


def select_device(devices: list[dict[str, Any]], device_id: str) -> dict[str, Any]:
    matches = [device for device in devices if device.get("deviceid") == device_id]
    if len(matches) != 1:
        raise EwelinkError(f"Expected one matching owned device, found {len(matches)}")
    device = matches[0]
    if not device.get("devicekey"):
        raise EwelinkError("Matching device did not include a devicekey")
    return device


def write_secret(path: Path, device: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "deviceid": device["deviceid"],
        "devicekey": device["devicekey"],
        "name": device.get("name"),
        "productModel": device.get("productModel"),
        "uiid": device.get("extra", {}).get("uiid"),
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
    finally:
        os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="prompted locally when omitted")
    parser.add_argument("--country-code", default="+44")
    parser.add_argument("--region", choices=sorted(API), default="eu")
    parser.add_argument("--device-id", help="defaults to the ID in the saved mDNS capture")
    parser.add_argument("--mdns-capture", type=Path, default=Path("captures/mdns-1.1.1.json"))
    parser.add_argument("--output", type=Path, default=Path("captures/device-key.json"))
    args = parser.parse_args()

    email = args.email or input("eWeLink email: ").strip()
    password = getpass.getpass("eWeLink password (not stored): ")
    if not email or not password:
        print("Email and password are required.", file=sys.stderr)
        return 2

    try:
        device_id = args.device_id or target_id_from_mdns(args.mdns_capture)
        region, token = login(email, password, args.country_code, args.region)
        device = select_device(get_devices(region, token), device_id)
        write_secret(args.output, device)
    except EwelinkError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        password = ""  # Minimize the lifetime of the credential reference.

    print(f"Saved the matching device key to {args.output} with mode 0600.")
    print("The account password and cloud access token were not stored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
