"""Prompts written for someone doing this for the first time.

Rules this module enforces so individual steps do not have to:
  - every question explains what it wants and where to find it;
  - a wrong answer is re-asked, never fatal;
  - answers are validated before the run continues;
  - "quit" always works, at every prompt, and stops cleanly.
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
import sys
import time
from typing import Callable, Iterable, Sequence


class QuitRun(RuntimeError):
    """The operator typed quit at a prompt."""


BULLET = "    "


def _emit(lines: Iterable[str]) -> None:
    for line in lines:
        print(f"{BULLET}{line}", flush=True)


def section(title: str, body: Sequence[str] = ()) -> None:
    print("", flush=True)
    print(f"-- {title}", flush=True)
    if body:
        _emit(body)


def _raw(question: str, default: str | None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    if answer.lower() in {"quit", "exit", "abort"}:
        raise QuitRun("stopped at an operator prompt")
    if not answer and default is not None:
        return default
    return answer


def ask_text(
    question: str,
    *,
    help_lines: Sequence[str] = (),
    default: str | None = None,
    example: str | None = None,
    validate: Callable[[str], tuple[bool, str]] | None = None,
) -> str:
    if help_lines:
        _emit(help_lines)
    if example:
        _emit([f"Example: {example}"])
    while True:
        answer = _raw(question, default)
        if not answer:
            _emit(["An answer is needed here. Type quit to stop."])
            continue
        if validate is None:
            return answer
        ok, message = validate(answer)
        if ok:
            return answer
        _emit([message])


def ask_yes_no(
    question: str,
    *,
    help_lines: Sequence[str] = (),
    default: bool | None = None,
) -> bool:
    if help_lines:
        _emit(help_lines)
    hint = {True: "Y/n", False: "y/N", None: "y/n"}[default]
    while True:
        answer = _raw(f"{question} ({hint})", None).lower()
        if not answer and default is not None:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        _emit(["Please answer y or n. Type quit to stop."])


def ask_choice(question: str, options: Sequence[str], *, help_lines: Sequence[str] = ()) -> int:
    """Returns the zero-based index of the chosen option."""
    if help_lines:
        _emit(help_lines)
    for index, option in enumerate(options, 1):
        _emit([f"{index}. {option}"])
    while True:
        answer = _raw(question, None)
        try:
            chosen = int(answer)
        except ValueError:
            _emit(["Type the number of one of the options above."])
            continue
        if 1 <= chosen <= len(options):
            return chosen - 1
        _emit([f"Choose a number between 1 and {len(options)}."])


def ping_ok(address: str, timeout: int = 2) -> bool:
    try:
        completed = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), address],
            capture_output=True, timeout=timeout + 3, check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ask_ipv4(
    question: str,
    *,
    help_lines: Sequence[str] = (),
    default: str | None = None,
    example: str | None = "192.168.1.50",
    must_respond: bool = False,
) -> str:
    """Ask for an address on the local network, and sanity-check it."""

    def validate(answer: str) -> tuple[bool, str]:
        try:
            address = ipaddress.ip_address(answer)
        except ValueError:
            return False, (
                f"'{answer}' is not an address. It should be four numbers "
                "separated by dots, like 192.168.1.50 - not a name and not an "
                "interface like eth0."
            )
        if address.version != 4:
            return False, "This needs an IPv4 address, like 192.168.1.50."
        if not address.is_private:
            return False, (
                f"{answer} is not a home-network address. Those normally start "
                "192.168., 10., or 172.16-31."
            )
        return True, ""

    while True:
        answer = ask_text(
            question, help_lines=help_lines, default=default,
            example=example, validate=validate,
        )
        help_lines = ()  # only explain once
        example = None
        if not must_respond or ping_ok(answer):
            return answer
        _emit([
            f"Nothing at {answer} answered a ping.",
            "That usually means the address is wrong, or the device is still",
            "starting up, or it is on a different network from this computer.",
        ])
        if ask_yes_no("Use it anyway?", default=False):
            return answer


def wait_for_host(
    address: str,
    *,
    what: str = "the plug",
    timeout: float = 240.0,
    poll: float = 5.0,
) -> bool:
    """Watch for a device coming back, instead of asking whether it has.

    Prints a live count so it never looks hung, and returns False on timeout so
    the caller can offer something more useful than failing.
    """
    print(f"{BULLET}Watching for {what} at {address} - this can take a minute.",
          flush=True)
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if ping_ok(address):
            elapsed = time.monotonic() - started
            sys.stdout.write("\r" + " " * 60 + "\r")
            print(f"{BULLET}{what.capitalize()} is back at {address} "
                  f"(after {elapsed:.0f}s).", flush=True)
            return True
        elapsed = int(time.monotonic() - started)
        sys.stdout.write(f"\r{BULLET}still waiting... {elapsed}s")
        sys.stdout.flush()
        time.sleep(poll)
    sys.stdout.write("\r" + " " * 60 + "\r")
    print(f"{BULLET}No answer from {address} after {timeout:.0f}s.", flush=True)
    return False


def wait_or_ask(
    address: str,
    *,
    what: str = "the plug",
    timeout: float = 240.0,
    help_lines: Sequence[str] = (),
) -> str:
    """Wait for `address`; if it never answers, offer the sensible ways out."""
    while True:
        if wait_for_host(address, what=what, timeout=timeout):
            return address
        choice = ask_choice(
            "What would you like to do",
            [
                "Keep waiting",
                f"{what.capitalize()} is on a different address - let me type it",
                "Stop here",
            ],
            help_lines=help_lines or [
                "It may still be starting up, or it may have been given a new",
                "address. Your router's list of connected devices will show it.",
            ],
        )
        if choice == 0:
            continue
        if choice == 1:
            return ask_ipv4(f"What address is {what} on now", must_respond=True)
        raise QuitRun(f"{what} did not come back")


def scan_wifi_ssids(timeout: float = 20.0) -> list[str]:
    """Names of Wi-Fi networks this machine can currently see.

    Best effort: returns an empty list when no supported scanner is present, so
    callers must treat "nothing found" as "do not know", never as "not there".
    """
    attempts = (
        ["nmcli", "-t", "-f", "SSID", "device", "wifi", "list", "--rescan", "auto"],
        ["nmcli", "-t", "-f", "SSID", "device", "wifi", "list"],
        ["iwlist", "scanning"],
    )
    for command in attempts:
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode != 0:
            continue
        names: list[str] = []
        for line in completed.stdout.splitlines():
            line = line.strip()
            if command[0] == "iwlist":
                if "ESSID:" in line:
                    names.append(line.split("ESSID:", 1)[1].strip().strip('"'))
            elif line:
                names.append(line)
        if names:
            return names
    return []


def find_ssid(prefix: str, timeout: float = 20.0) -> str | None:
    """The first visible network whose name starts with `prefix`, if any."""
    for name in scan_wifi_ssids(timeout=timeout):
        if name.startswith(prefix):
            return name
    return None


def wait_for_ssid(prefix: str, *, timeout: float = 90.0, poll: float = 8.0) -> str | None:
    """Watch for a network to appear, e.g. a device rebooting into setup mode."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        found = find_ssid(prefix, timeout=poll * 2)
        if found:
            return found
        elapsed = int(time.monotonic() - started)
        sys.stdout.write(f"\r{BULLET}looking for a '{prefix}' network... {elapsed}s")
        sys.stdout.flush()
        time.sleep(poll)
    sys.stdout.write("\r" + " " * 60 + "\r")
    return None


def resolve_host(name: str) -> str | None:
    """The address a device name resolves to, if the network can answer.

    Many routers publish DHCP hostnames in local DNS, and most desktop Linux
    resolves .local through mDNS, so a Tasmota device is often reachable by name
    without anyone reading an address off a router page.
    """
    for candidate in (name, f"{name}.local"):
        try:
            address = socket.gethostbyname(candidate)
        except OSError:
            continue
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.version == 4 and parsed.is_private:
            return address
    return None
