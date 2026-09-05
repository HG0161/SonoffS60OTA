"""Drive the reviewed migration phase servers without console pasting.

This starts tools/serve_safeboot_migration.py exactly as the runbook does,
reads the URL and arm token it prints, and delivers the loader to the plug over
Tasmota's ordinary command endpoint.  It changes the transport only: the Berry
that executes on the device is byte-for-byte the reviewed template the server
renders, and the host server remains the sole judge of PASS.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from .device import Device

ARMED_PHASES = {"commit", "restore"}
CLOSURE_NAME = {"commit": "s60_commit", "restore": "s60_restore"}

LOADER_RE = re.compile(r"s60_urlbeload\('(?P<url>http://[^']+)'\)")
# The server prints instructions for a human pasting into the Berry console.
# This tool delivers the same program over HTTP instead, so echoing them would
# invite someone to run it a second time - next to the irreversible step.
MANUAL_INSTRUCTION_RE = re.compile(
    r"paste this as one line|do not prefix it with|paste the loader|"
    r"^\s*def s60_urlbeload|^\s*s60_(commit|restore)\(|"
    r"loading performs no writes|arm separately with",
    re.IGNORECASE,
)
ARM_RE = re.compile(r"(?P<name>s60_(?:commit|restore))\(\"(?P<token>[^\"]+)\"\)")


class PhaseError(RuntimeError):
    pass


def short_loader(url: str, assign_to: str | None = None) -> str:
    """A compact equivalent of the server's printed loader.

    The printed loader defines a helper function first, which makes the command
    line long enough to risk Tasmota's console buffer.  This fetches and
    compiles the same served program in far fewer characters.
    """
    fetch = f"var c=webclient() c.begin('{url}') c.GET() "
    if assign_to:
        return fetch + f"{assign_to}=compile(c.get_string())()"
    return fetch + "compile(c.get_string())()"


class PhaseRunner:
    def __init__(
        self,
        repo_root: Path,
        manifest: Path,
        evidence_dir: Path,
        listen_ip: str,
        device: Device,
        listen_port: int = 8089,
        log: Callable[[str], None] = print,
    ) -> None:
        self.repo_root = repo_root
        self.manifest = manifest
        self.evidence_dir = evidence_dir
        self.listen_ip = listen_ip
        self.device = device
        self.listen_port = listen_port
        self.log = log

    def _command(self, phase: str, extra_flags: list[str]) -> list[str]:
        return [
            sys.executable,
            "-u",
            str(self.repo_root / "tools" / "serve_safeboot_migration.py"),
            phase,
            "--manifest",
            str(self.manifest),
            "--evidence-dir",
            str(self.evidence_dir),
            "--listen-ip",
            self.listen_ip,
            "--listen-port",
            str(self.listen_port),
            "--device-ip",
            self.device.host,
            *extra_flags,
        ]

    def run(
        self,
        phase: str,
        extra_flags: list[str] | None = None,
        confirm: Callable[[str], bool] | None = None,
        exec_timeout: float = 1800.0,
    ) -> tuple[int, str]:
        """Run one phase end to end.  Returns (exit code, captured output)."""
        if phase in ARMED_PHASES and confirm is None:
            raise PhaseError(f"{phase} requires an explicit confirmation callback")

        process = subprocess.Popen(
            self._command(phase, extra_flags or []),
            cwd=str(self.repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        captured: list[str] = []
        url: str | None = None
        arm: tuple[str, str] | None = None

        def pump() -> None:
            nonlocal url, arm
            assert process.stdout is not None
            for line in process.stdout:
                captured.append(line.rstrip("\n"))
                if not MANUAL_INSTRUCTION_RE.search(line):
                    self.log(f"  server| {line.rstrip()}")
                if url is None:
                    found = LOADER_RE.search(line)
                    if found:
                        url = found.group("url")
                if arm is None:
                    found = ARM_RE.search(line)
                    if found:
                        arm = (found.group("name"), found.group("token"))

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()

        deadline = 30.0
        waited = 0.0
        while url is None and waited < deadline and process.poll() is None:
            reader.join(0.5)
            waited += 0.5
        if url is None:
            process.terminate()
            reader.join(5.0)
            raise PhaseError(
                f"{phase} server did not print a loader URL:\n" + "\n".join(captured)
            )

        if phase in ARMED_PHASES:
            while arm is None and process.poll() is None and waited < deadline + 10.0:
                reader.join(0.5)
                waited += 0.5
            if arm is None:
                process.terminate()
                raise PhaseError(f"{phase} server did not print an arm command")

            closure = CLOSURE_NAME[phase]
            self.log("Sending the instructions to the plug. This writes nothing yet.")
            self.device.berry_fire(short_loader(url, assign_to=closure), timeout=60.0)

            if not confirm(phase):  # type: ignore[misc]
                process.terminate()
                reader.join(5.0)
                raise PhaseError(f"{phase} was not confirmed; nothing was armed")

            name, token = arm
            self.log("Telling the plug to start. Leave it powered and connected.")
            self.device.berry_fire(f'{name}("{token}")', timeout=60.0)
        else:
            self.log("Sending the instructions to the plug and starting.")
            self.device.berry_fire(short_loader(url), timeout=60.0)

        try:
            code = process.wait(timeout=exec_timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            reader.join(5.0)
            raise PhaseError(f"{phase} did not complete within {exec_timeout:.0f}s")
        reader.join(5.0)
        return code, "\n".join(captured)
