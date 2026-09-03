#!/usr/bin/env python3
"""Query genuine eWeLink OTA metadata without triggering an upgrade."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from tools.get_device_key import (
        API,
        EwelinkError,
        api_json,
        get_devices,
        login,
        select_device,
        target_id_from_mdns,
    )
except ModuleNotFoundError:  # Support direct execution as tools/query_ota.py.
    from get_device_key import (
        API,
        EwelinkError,
        api_json,
        get_devices,
        login,
        select_device,
        target_id_from_mdns,
    )


def ota_identity(device: dict[str, Any]) -> dict[str, str]:
    model = (
        device.get("extra", {}).get("model")
        or device.get("model")
        or device.get("productModel")
    )
    version = device.get("params", {}).get("fwVersion")
    if not model or not version:
        raise EwelinkError("Cloud device record did not contain OTA model and firmware version")
    return {"deviceid": device["deviceid"], "model": model, "version": version}


def query_ota(region: str, token: str, identity: dict[str, str]) -> dict[str, Any]:
    body = json.dumps({"deviceInfoList": [identity]}).encode("utf-8")
    response = api_json(
        API[region] + "/v2/device/ota/query",
        body=body,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "User-Agent": "s60-ota-research/0.1",
        },
    )
    if response.get("error") != 0:
        raise EwelinkError("OTA metadata query failed: " + str(response.get("msg", "unknown error")))
    return response


def private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
    finally:
        os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="prompted locally when omitted")
    parser.add_argument("--country-code", default="+44")
    parser.add_argument("--region", choices=sorted(API), default="eu")
    parser.add_argument("--device-id")
    parser.add_argument("--mdns-capture", type=Path, default=Path("captures/mdns-1.1.1.json"))
    parser.add_argument("--output", type=Path, default=Path("captures/ota-metadata-1.2.0.json"))
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
        identity = ota_identity(device)
        response = query_ota(region, token, identity)
        private_json(args.output, {"query": identity, "response": response})
    except EwelinkError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        password = ""

    ota_list = response.get("data", {}).get("otaInfoList", [])
    print(f"Saved OTA metadata to {args.output} with mode 0600.")
    print(f"Available OTA records: {len(ota_list)}")
    for ota in ota_list:
        print(f"Version: {ota.get('version')} | files: {len(ota.get('binList', []))}")
    print("No upgrade command was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

