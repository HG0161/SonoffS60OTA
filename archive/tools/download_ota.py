#!/usr/bin/env python3
"""Download vendor OTA artifacts and require their manifest SHA-256 digests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


MAX_FILE_SIZE = 16 * 1024 * 1024


class DownloadError(RuntimeError):
    pass


def ota_files(metadata: dict[str, Any], version: str | None = None) -> tuple[str, list[dict[str, str]]]:
    infos = metadata.get("response", {}).get("data", {}).get("otaInfoList", [])
    if version:
        infos = [info for info in infos if info.get("version") == version]
    if len(infos) != 1:
        raise DownloadError(f"Expected one matching OTA record, found {len(infos)}")
    selected = infos[0]
    files = selected.get("binList", [])
    if not files:
        raise DownloadError("OTA record has no files")
    for item in files:
        name = item.get("name", "")
        digest = item.get("digest", "")
        url = item.get("downloadUrl", "")
        if not name or Path(name).name != name:
            raise DownloadError("Unsafe or missing OTA filename")
        if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
            raise DownloadError(f"Invalid SHA-256 digest for {name}")
        if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
            raise DownloadError(f"Unsupported URL scheme for {name}")
    return selected["version"], files


def download_one(item: dict[str, str], directory: Path, timeout: float = 30.0) -> dict[str, Any]:
    name = item["name"]
    expected = item["digest"].lower()
    target = directory / name
    if target.exists():
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual == expected:
            return {"name": name, "size": target.stat().st_size, "sha256": actual, "reused": True}
        raise DownloadError(f"Existing {name} does not match the manifest; refusing to overwrite it")

    request = urllib.request.Request(item["downloadUrl"], headers={"User-Agent": "s60-ota-research/0.1"})
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.URLError as exc:
        raise DownloadError(f"Download failed for {name}: {exc.reason}") from None

    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_FILE_SIZE:
        response.close()
        raise DownloadError(f"Manifest file {name} exceeds the safety size limit")

    digest = hashlib.sha256()
    size = 0
    descriptor, temporary_name = tempfile.mkstemp(prefix=name + ".", suffix=".part", dir=directory)
    try:
        os.chmod(temporary_name, 0o600)
        with os.fdopen(descriptor, "wb") as stream, response:
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise DownloadError(f"Manifest file {name} exceeds the safety size limit")
                digest.update(chunk)
                stream.write(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise DownloadError(f"SHA-256 mismatch for {name}")
        os.replace(temporary_name, target)
        os.chmod(target, 0o600)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return {"name": name, "size": size, "sha256": actual, "reused": False}


def private_json(path: Path, value: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
    finally:
        os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=Path("captures/ota-metadata-1.2.0.json"))
    parser.add_argument("--version")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    version, files = ota_files(metadata, args.version)
    directory = args.output_dir or Path("captures") / ("vendor-" + version)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    results = [download_one(item, directory) for item in files]
    report = {"version": version, "files": results}
    private_json(directory / "verified-files.json", report)
    for result in results:
        print(f"Verified {result['name']}: {result['size']} bytes, SHA-256 matches")
    print(f"Saved {len(results)} verified files under {directory}")
    print("No upgrade command was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

