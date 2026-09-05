"""Write Wi-Fi credentials into a prebuilt firmware image.

The plug spends a few minutes running a small startup firmware that has nowhere
to read Wi-Fi details from, so those details have to be inside the image. Asking
every user to compile one is a wall most people will not climb.

Instead the image is built once with padded placeholders in place of the network
name and password, and this writes the real values into that reserved space. The
result is a valid ESP32 application image: both integrity fields the bootloader
checks - the one-byte XOR over segment data and the appended SHA-256 - are
recalculated afterwards.

Nothing here relaxes a check. The patched image is validated exactly as any
downloaded artifact is, and the migration still refuses it unless the plug reads
back byte-for-byte what was sent.
"""

from __future__ import annotations

import hashlib
import re

try:
    from tools.analyze_vendor_ota import parse_esp_image
except ModuleNotFoundError:  # running from inside tools/
    from analyze_vendor_ota import parse_esp_image


class ImprintError(RuntimeError):
    pass


# Written into the image at build time. Each is followed by enough reserved
# bytes to hold the longest value 802.11 allows, so the real value never needs
# more room than the placeholder already occupies.
SSID_MARKER = b"S60-IMPRINT-SSID>"
PASSWORD_MARKER = b"S60-IMPRINT-PASS>"
SSID_FIELD_BYTES = 33  # 32 characters plus a terminator
PASSWORD_FIELD_BYTES = 64  # 63 characters plus a terminator
# The reserved space is filled with this at build time. It must not be NUL, or
# the compiler would end the string literal early and allocate too little.
FILLER = b"."


def find_field(image: bytes, marker: bytes, field_bytes: int) -> int:
    """Offset of the reserved space that follows `marker`, exactly once."""
    first = image.find(marker)
    if first < 0:
        raise ImprintError(
            f"this image has no {marker.decode()} placeholder, so it was not "
            "built to have credentials written into it"
        )
    if image.find(marker, first + 1) >= 0:
        raise ImprintError(
            f"{marker.decode()} appears more than once; refusing to guess which "
            "one holds the credential"
        )
    start = first + len(marker)
    if start + field_bytes > len(image):
        raise ImprintError(f"{marker.decode()} is too close to the end of the image")
    return start


def write_field(image: bytearray, offset: int, value: str, field_bytes: int) -> None:
    encoded = value.encode("utf-8")
    if len(encoded) >= field_bytes:
        raise ImprintError(
            f"{len(encoded)} bytes is too long for this field, which reserves "
            f"{field_bytes - 1} characters"
        )
    image[offset : offset + field_bytes] = encoded.ljust(field_bytes, b"\0")


def repair_image(image: bytes) -> bytes:
    """Recompute the two integrity fields the ESP32 bootloader verifies."""
    report = parse_esp_image(image)
    patched = bytearray(image)

    checksum = 0xEF
    for segment in report["segments"]:
        start = segment["data_offset"]
        for value in patched[start : start + segment["size"]]:
            checksum ^= value
    patched[report["checksum_offset"]] = checksum

    if report["hash_appended"]:
        end = report["checksum_offset"] + 1
        patched[end : end + 32] = hashlib.sha256(bytes(patched[:end])).digest()
    return bytes(patched)


def imprint(image: bytes, ssid: str, password: str) -> bytes:
    """Return the image with these credentials written in and its hashes fixed."""
    if not ssid:
        raise ImprintError("a network name is required")
    ssid_at = find_field(image, SSID_MARKER, SSID_FIELD_BYTES)
    password_at = find_field(image, PASSWORD_MARKER, PASSWORD_FIELD_BYTES)

    patched = bytearray(image)
    write_field(patched, ssid_at, ssid, SSID_FIELD_BYTES)
    write_field(patched, password_at, password, PASSWORD_FIELD_BYTES)
    result = repair_image(bytes(patched))

    report = parse_esp_image(result)
    if not report["checksum_valid"] or report["sha256_valid"] is False:
        raise ImprintError("the patched image failed its own validation")
    if len(result) != len(image):
        raise ImprintError("patching changed the image length")
    return result


BUILD_PATH_PATTERN = re.compile(rb"/(?:home|Users)/[A-Za-z0-9._-]+/[^\x00]{0,200}")


def find_build_paths(image: bytes) -> list[bytes]:
    """Absolute paths from the build machine left in the image.

    Compilers bake __FILE__ into assertion and log strings. On a personal
    machine those carry a username and directory layout, which has no business
    being in a published binary.
    """
    return sorted({match.group(0) for match in BUILD_PATH_PATTERN.finditer(image)})


def redact_build_paths(image: bytes, replacement: bytes = b"/build") -> bytes:
    """Overwrite build paths in place, then repair the image.

    The replacement is written into the same bytes and NUL-padded, so the string
    stays valid and nothing moves - only a log message becomes less specific.
    """
    patched = bytearray(image)
    for path in find_build_paths(bytes(image)):
        start = 0
        while True:
            at = patched.find(path, start)
            if at < 0:
                break
            tail = path.rsplit(b"/", 1)[-1]
            neutral = (replacement + b"/" + tail)[: len(path)]
            patched[at : at + len(path)] = neutral.ljust(len(path), b"\0")
            start = at + len(path)
    return repair_image(bytes(patched))


def has_placeholders(image: bytes) -> bool:
    """True when the reserved space has never been written to.

    The markers survive imprinting - they are how the field is located, and
    keeping them lets an image be rewritten for a different network. So being
    written or not is decided by the reserved space itself: untouched, it is
    still entirely filler, which no real credential can be, because a value that
    long would exceed what 802.11 permits.
    """
    try:
        start = find_field(image, SSID_MARKER, SSID_FIELD_BYTES)
    except ImprintError:
        return False
    return image[start : start + SSID_FIELD_BYTES] == FILLER * SSID_FIELD_BYTES


def require_imprinted(image: bytes) -> None:
    """Refuse an image whose credentials were never written in.

    Such an image is not merely useless: it would boot looking for a network
    named after the placeholder, retry forever, and never raise an access point,
    leaving the plug unreachable at the one moment it must not be.
    """
    if has_placeholders(image):
        raise ImprintError(
            "this image still has its credential placeholders - it has not had "
            "a network name and password written into it, and installing it "
            "would leave the plug unable to reach any network"
        )


def blank_variable_regions(image: bytes) -> bytes:
    """The image with credentials and integrity fields zeroed.

    Two copies of the same published firmware, imprinted with different
    credentials, differ only in the reserved fields and in the checksum and hash
    that cover them. Blanking those makes the rest directly comparable.
    """
    blanked = bytearray(image)
    for marker, field in ((SSID_MARKER, SSID_FIELD_BYTES), (PASSWORD_MARKER, PASSWORD_FIELD_BYTES)):
        try:
            at = find_field(bytes(image), marker, field)
        except ImprintError:
            continue
        blanked[at : at + field] = b"\0" * field
    report = parse_esp_image(image)
    blanked[report["checksum_offset"]] = 0
    if report["hash_appended"]:
        end = report["checksum_offset"] + 1
        blanked[end : end + 32] = b"\0" * 32
    return bytes(blanked)


def derived_from(candidate: bytes, published: bytes) -> bool:
    """True when `candidate` is `published` with credentials written into it.

    Proves provenance without needing the credentials: everything outside the
    reserved fields must be identical, so no other code can have been slipped in.
    """
    if len(candidate) != len(published):
        return False
    return blank_variable_regions(candidate) == blank_variable_regions(published)
