#!/usr/bin/env python3
"""Take a stock eWeLink Sonoff S60 to the canonical Tasmota layout.

This orchestrates the reviewed tools in tools/.  It reimplements no validator,
no Berry phase and no safety gate: every destructive action still runs the same
audited program, behind the same locks, judged by the same host-side evidence.
What it removes is console pasting, transcription error and lost place.

Every step records itself in captures/<plug>/autoflash-state.json, so an
interrupted run resumes where it stopped.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.autoflash.device import Device, DeviceError  # noqa: E402
from tools.autoflash.phases import PhaseRunner  # noqa: E402
from tools.autoflash.prompts import (  # noqa: E402
    QuitRun,
    ask_choice,
    ask_ipv4,
    ask_text,
    ask_yes_no,
    find_ssid,
    resolve_host,
    section,
    wait_for_ssid,
    wait_or_ask,
)
from tools.autoflash.state import ATTEMPTED, DONE, FAILED, RunState  # noqa: E402
from tools.query_ota import ota_endpoints  # noqa: E402
from tools.safeboot_migration import (  # noqa: E402
    APP0_OFFSET,
    SAFEBOOT_OFFSET,
    TABLE_OFFSET,
    TABLE_SIZE,
    load_manifest,
    verify_manifest_files,
)

DEFAULT_MANIFEST = Path("captures/safeboot-recovery-migration/manifest.json")
S60_TEMPLATE = (
    '{"NAME":"Sonoff S60TPG","GPIO":[1,1,1,1,224,544,1,3104,1,32,1,0,0,0,0,0,0,0,'
    '1,1,1,1],"FLAG":0,"BASE":1}'
)
COMMIT_PHRASE = "replace the partition table"
BRIDGE_AP_PREFIX = "S60-OTA-Bridge"
TASMOTA_AP_PREFIX = "tasmota-"


class Abort(RuntimeError):
    pass


# ---------------------------------------------------------------- utilities


def say(message: str = "") -> None:
    print(message, flush=True)


def rule(title: str) -> None:
    say("")
    say(f"== {title}")


def ask(prompt: str) -> str:
    return ask_text(prompt)


def confirm(prompt: str) -> bool:
    return ask_yes_no(prompt, default=False)


def require_phrase(phrase: str) -> bool:
    say("")
    say("To go ahead, type this line exactly, then Enter:")
    say(f"    {phrase}")
    say("Anything else - including y - stops here and changes nothing.")
    return ask_text(">", validate=lambda a: (True, "")) == phrase


def run_tool(
    args: list[str],
    cwd: Path = ROOT,
    interactive: bool = False,
    env: dict[str, str] | None = None,
) -> int:
    say(f"$ {' '.join(str(a) for a in args)}")
    if interactive:
        return subprocess.call(args, cwd=str(cwd), env=env)
    completed = subprocess.run(args, cwd=str(cwd), text=True, env=env)
    return completed.returncode


def route_towards(target: str) -> tuple[str | None, str | None]:
    """Ask the kernel which interface and source address reach `target`.

    `ip route get` answers both in one line, e.g.
      192.168.1.185 dev enp3s0 src 192.168.1.57 uid 1000
    so neither has to be typed or guessed on a machine with several interfaces.
    """
    try:
        completed = subprocess.run(
            ["ip", "route", "get", target],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if completed.returncode != 0:
        return None, None
    fields = completed.stdout.split()
    device = source = None
    for index, field in enumerate(fields[:-1]):
        if field == "dev":
            device = fields[index + 1]
        elif field == "src":
            source = fields[index + 1]
    return device, source


def local_ip_towards(target: str) -> str:
    """The address this machine would use to reach `target`.

    A UDP connect sends nothing; it just makes the kernel pick the route and
    therefore the correct source interface.  That beats guessing when a machine
    has several - ethernet, Wi-Fi, docker bridges, VPN.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((target, 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


def run_directories() -> list[Path]:
    captures = ROOT / "captures"
    if not captures.is_dir():
        return []
    return sorted(
        (child for child in captures.iterdir() if (child / "autoflash-state.json").is_file()),
        key=lambda child: (child / "autoflash-state.json").stat().st_mtime,
    )


def run_is_complete(directory: Path) -> bool:
    try:
        state = RunState.open(directory)
    except (OSError, ValueError):
        return False
    return state.is_done(STEPS[-1][0])


CURRENT_POINTER = ROOT / "captures" / ".autoflash-current"


def remember_current(name: str) -> None:
    try:
        CURRENT_POINTER.parent.mkdir(parents=True, exist_ok=True)
        CURRENT_POINTER.write_text(name + "\n", encoding="utf-8")
    except OSError:
        pass  # the pointer is a convenience, never a requirement


def read_current() -> str | None:
    try:
        name = CURRENT_POINTER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


def describe_run(directory: Path) -> str:
    state = RunState.open(directory)
    done = sum(1 for step, _, _ in STEPS if state.is_done(step))
    where = state.input("plug_ip") or "no address yet"
    when = state.data.get("updated_utc", "")[:16].replace("T", " ")
    return f"{where:<16} {done}/{len(STEPS)} steps done   last touched {when}"


def new_run_name(plug_ip: str | None) -> str:
    """A name the operator never has to type, but can recognise if they look."""
    if plug_ip:
        return "plug-" + plug_ip.replace(".", "-")
    return "conversion-" + time.strftime("%Y%m%d-%H%M")


def resolve_run_name(requested: str | None, force_new: bool, plug_ip: str | None) -> str:
    """Work out which conversion this is, without asking for an identifier.

    Mirrors how resumable tools normally behave: one in-flight operation,
    remembered in a well-known place, and a numbered menu if that is genuinely
    ambiguous.  Nothing opaque is ever typed by the operator.
    """
    if requested:
        remember_current(requested)
        return requested

    unfinished = [d for d in run_directories() if not run_is_complete(d)]

    if force_new:
        name = new_run_name(plug_ip)
        suffix = 2
        existing = {d.name for d in run_directories()}
        while name in existing:
            name = f"{new_run_name(plug_ip)}-{suffix}"
            suffix += 1
        remember_current(name)
        return name

    pointer = read_current()
    if pointer and any(d.name == pointer for d in unfinished):
        say(f"Continuing the conversion of {RunState.open(ROOT / 'captures' / pointer).input('plug_ip') or pointer}.")
        return pointer

    if not unfinished:
        name = new_run_name(plug_ip)
        remember_current(name)
        return name

    if len(unfinished) == 1:
        name = unfinished[0].name
        say(f"Continuing the conversion of {RunState.open(unfinished[0]).input('plug_ip') or name}.")
        remember_current(name)
        return name

    options = [describe_run(d) for d in unfinished] + ["Start a new conversion"]
    chosen = ask_choice(
        "Which one",
        options,
        help_lines=["There is more than one conversion part way through."],
    )
    if chosen == len(unfinished):
        name = new_run_name(plug_ip)
        remember_current(name)
        return name
    remember_current(unfinished[chosen].name)
    return unfinished[chosen].name


class Context:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.plug = args.run
        self.dir = ROOT / "captures" / self.plug
        self.evidence = self.dir / "live"
        self.state = RunState.open(self.dir)
        self.manifest_path = (ROOT / args.manifest).resolve()
        self.dry_run = args.dry_run
        if args.plug_ip:
            self.state.set_input("plug_ip", args.plug_ip)
        if args.listen_ip:
            self.state.set_input("listen_ip", args.listen_ip)
        if args.email:
            self.state.set_input("email", args.email)
        if args.web_user:
            self.state.set_input("web_user", args.web_user)
        self.web_password = args.web_password
        self._ewelink_password: str | None = None

    def ewelink_env(self) -> dict[str, str]:
        """Ask for the account password once and reuse it for this run only.

        It is held in memory, never written to the run state, and never put on a
        command line.  It reaches the reviewed tools through EWELINK_PASSWORD,
        which serve_tasmota_ota.py already supported.
        """
        if self._ewelink_password is None:
            self._ewelink_password = getpass.getpass(
                "eWeLink password (asked once for this run, not stored): "
            )
        return {**os.environ, "EWELINK_PASSWORD": self._ewelink_password}

    def listen_ip(self) -> str:
        """Where the plug fetches from: given, remembered, or derived."""
        existing = self.state.input("listen_ip")
        if existing:
            return existing
        plug_ip = self.state.input("plug_ip")
        if not plug_ip:
            raise Abort("the plug address is not known yet, so the workstation "
                        "address cannot be derived; pass --listen-ip")
        _, routed = route_towards(plug_ip)
        try:
            derived = routed or local_ip_towards(plug_ip)
        except OSError as exc:
            raise Abort(
                f"could not work out how this machine reaches {plug_ip} ({exc}); "
                "check you are on the same network as the plug, or pass --listen-ip"
            ) from exc
        address = ipaddress.ip_address(derived)
        if not (address.is_private and address.version == 4):
            raise Abort(f"derived workstation address {derived} is not a private IPv4 LAN address")
        say(f"Workstation address towards {plug_ip}: {derived}")
        self.state.set_input("listen_ip", derived)
        return derived

    @property
    def device(self) -> Device:
        host = self.state.input("plug_ip")
        if not host:
            raise Abort("plug_ip is not set; pass --plug-ip")
        return Device(
            host,
            user=self.state.input("web_user"),
            password=self.web_password,
        )

    def phase_runner(self) -> PhaseRunner:
        return PhaseRunner(
            repo_root=ROOT,
            manifest=self.manifest_path,
            evidence_dir=self.evidence,
            listen_ip=self.listen_ip(),
            device=self.device,
            log=say,
        )


# -------------------------------------------------------------------- steps


def upload_or_prompt(ctx: "Context", path: Path, what: str) -> str:
    """Upload over HTTP, falling back to the operator's browser.

    The multipart path has not been exercised on this hardware.  A failure here
    must not strand a run: the same file uploaded through the web UI is
    identical, so offer that and carry on.
    """
    device = ctx.device
    try:
        started = time.monotonic()
        device.upload_firmware(path)
        seconds = round(time.monotonic() - started, 1)
        say(f"{what} uploaded in {seconds}s")
        return f"http:{seconds}s"
    except Exception as exc:  # noqa: BLE001 - any failure falls back to the operator
        say(f"Automatic upload failed: {exc}")
        say("")
        say(f"Upload it by hand instead - the file is identical:")
        say(f"  open http://{device.host}/  ->  Firmware Upgrade  ->  choose file")
        say(f"  {path}")
        if not confirm(f"{what} uploaded through the web UI?"):
            raise Abort(f"{what} was not uploaded")
        return "manual"


def step_check(ctx: Context) -> dict[str, Any]:
    """A5 - offline preconditions.  Touches nothing."""
    manifest = load_manifest(ctx.manifest_path)
    verify_manifest_files(ctx.manifest_path, manifest)
    say("manifest and pinned artifacts: OK")

    code = run_tool(["sha256sum", "-c", "SHA256SUMS"])
    if code != 0:
        raise Abort("artifact checksums failed")

    for lock in ("RECOVERY_LOCK", "REPARTITION_LOCK"):
        if not (ROOT / lock).exists():
            raise Abort(f"{lock} is not in place; refusing to start from an unsafe state")
    say("both safety locks in place")

    for phase in ("preflight", "stage", "commit", "restore"):
        report = ctx.evidence / f"{phase}-report.json"
        if report.exists():
            say(f"note: existing {phase} evidence in {ctx.evidence}")
    return {"mode": manifest.get("migration_mode", "official")}


def step_pair(ctx: Context) -> dict[str, Any]:
    """A1 - operator action."""
    section("Set the plug up in the eWeLink app", [
        "Do these in the phone app before answering:",
        "",
        "1. Plug the S60 into a socket you can reach.",
        "2. In eWeLink, add it as a new device. It must join the same 2.4 GHz",
        "   Wi-Fi network this computer is on - the plug cannot use 5 GHz.",
        "3. Open the device, go into its settings, and turn ON 'LAN Control'.",
        "4. In your router's admin page, find the plug in the list of connected",
        "   devices and reserve its address so it cannot change later.",
        "",
        "The address is shown in your router's device list, and in eWeLink under",
        "the device's settings or information page.",
    ])
    ip = ask_ipv4(
        "What address did the plug get",
        help_lines=["Four numbers with dots. Not the plug's name."],
        must_respond=True,
    )
    ctx.state.set_input("plug_ip", ip)
    return {"plug_ip": ip}


def step_discover(ctx: Context) -> dict[str, Any]:
    """A2/A3/A4 - identify the device and read its OTA metadata."""
    plug_ip = ctx.state.require("plug_ip")[0]
    mdns = ctx.dir / "mdns.json"
    meta = ctx.dir / "ota-metadata.json"
    key = ctx.dir / "device-key.json"

    if run_tool([sys.executable, "tools/discover_ewelink.py", "--timeout", "8",
                 "--target", plug_ip, "--output", str(mdns)]) != 0:
        raise Abort("discovery failed")
    env = ctx.ewelink_env()
    if run_tool([sys.executable, "tools/query_ota.py", "--mdns-capture", str(mdns),
                 "--output", str(meta)], interactive=True, env=env) != 0:
        raise Abort("OTA metadata query failed")
    if run_tool([sys.executable, "tools/get_device_key.py", "--mdns-capture", str(mdns),
                 "--output", str(key)], interactive=True, env=env) != 0:
        raise Abort("device key retrieval failed")

    # Read what query_ota just wrote instead of asking the operator to retype it.
    try:
        metadata = json.loads(meta.read_text(encoding="utf-8"))
        version = str(metadata["query"]["version"])
        model = str(metadata["query"].get("model", ""))
        endpoints = ota_endpoints(metadata["response"]["data"]["otaInfoList"])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise Abort(f"could not read the OTA metadata just written: {exc}") from exc

    if not endpoints:
        raise Abort("the OTA metadata contains no vendor download endpoint to redirect")
    if len(endpoints) > 1:
        say("The metadata names more than one vendor endpoint:")
        for index, (host, port) in enumerate(endpoints, 1):
            say(f"  {index}. {host}:{port}")
        choice = ask("Which one does the router rule target? [number]")
        try:
            vendor_ip, vendor_port_int = endpoints[int(choice) - 1]
        except (ValueError, IndexError) as exc:
            raise Abort("no valid endpoint chosen") from exc
    else:
        vendor_ip, vendor_port_int = endpoints[0]
    vendor_port = str(vendor_port_int)

    section("What the plug reported", [
        f"Model            {model}",
        f"Firmware now     {version}",
        f"Update server    {vendor_ip} port {vendor_port}",
        "",
        "The update server is the address the plug would normally download",
        "Sonoff firmware from. The next step redirects just that one connection",
        "to this computer, so the plug downloads our firmware instead.",
    ])
    if not ask_yes_no(
        "Does that match the lines printed just above",
        help_lines=["Answer n if anything looks different and we will stop."],
        default=True,
    ):
        raise Abort("metadata not confirmed")

    ctx.state.set_input("stock_version", version)
    ctx.state.set_input("vendor_ip", vendor_ip)
    ctx.state.set_input("vendor_port", vendor_port)
    return {"stock_version": version, "vendor": f"{vendor_ip}:{vendor_port}", "model": model}


def step_intercept(ctx: Context) -> dict[str, Any]:
    """B1/B2 - router DNAT, then prove it before anything is written."""
    plug_ip, vendor_ip, vendor_port = ctx.state.require("plug_ip", "vendor_ip", "vendor_port")
    listen_ip = ctx.listen_ip()
    section("Redirect the plug's update connection", [
        "You now need one rule in your router. It sends the plug's firmware",
        "download to this computer instead of to Sonoff's server. Nothing else",
        "on your network is affected, and you remove the rule at the end.",
        "",
        "In the router admin page look for a section called any of:",
        "  Port Forwarding / NAT / Virtual Server / Destination NAT / DNAT",
        "(On OpenWrt it is Network > Firewall > Port Forwards.)",
        "",
        "Create a rule with these values:",
        f"  Protocol            TCP",
        f"  Source address      {plug_ip}          (the plug)",
        f"  Destination address {vendor_ip}",
        f"  Destination port    {vendor_port}",
        f"  Forward to address  {listen_ip}          (this computer)",
        f"  Forward to port     {vendor_port}",
        "",
        "If your router has an option called NAT reflection, hairpin NAT or",
        "masquerading, turn it on. Without it the reply can reach the plug from",
        "the wrong address and the plug ignores it.",
        "",
        "If you cannot find this in your router, stop here and ask - a normal",
        "'port forward from the internet' is NOT the same thing and will not work.",
    ])
    if not ask_yes_no("Have you saved that rule", default=False):
        raise Abort("the redirect rule is needed before anything can be installed")

    # The kernel already knows which network connection reaches the plug, so
    # this is stated rather than asked: an operator has no way to check it.
    interface, _ = route_towards(plug_ip)
    if interface:
        say(f"This computer reaches the plug over its {interface} connection.")
    else:
        section("Which network connection reaches the plug", [
            "This computer could not work that out by itself, so it needs telling.",
            "Below is a list of this machine's network connections. Find the one",
            "holding an address on the same network as the plug - the one starting",
            f"with the same numbers as {plug_ip} - and type its name, the short word",
            "on the left such as eth0, enp34s0 or wlan0.",
        ])
        subprocess.run(["ip", "-brief", "address"], check=False)
        interface = ask_text(
            "Connection name",
            example="enp34s0",
            validate=lambda answer: (
                (False, "That looks like an address. Type the short name on the "
                        "left of the list instead, such as enp34s0.")
                if ("." in answer or ":" in answer)
                else (True, "")
            ),
        )
    section("Test the rule before touching the plug", [
        "The test borrows the plug's address for a few seconds to check the",
        "redirect really works. The plug must be switched off first, or two",
        "devices would answer to the same address.",
        "",
        "Pull the plug out of the socket now. Leave it out until asked.",
    ])
    if not ask_yes_no("Is the plug unplugged from the wall", default=False):
        raise Abort("the plug must be powered off for this test")
    say("")
    say("You will be asked for your computer's password - the test needs")
    say("administrator rights to add and remove a temporary address.")

    code = run_tool(
        ["sudo", "./tools/test_ota_firewall.sh", interface, plug_ip, listen_ip,
         vendor_ip, vendor_port],
        interactive=True,
    )
    if code != 0:
        raise Abort(
            "the redirect test failed. The rule is not working yet, and sending "
            "the upgrade command now would make the plug install Sonoff's own "
            "firmware instead. Fix the rule and run this step again."
        )
    section("Test passed", [
        "Plug the S60 back into the wall. Nothing else to do - this will notice",
        "when it reconnects.",
    ])
    found = wait_or_ask(plug_ip, what="the plug")
    if found != plug_ip:
        ctx.state.set_input("plug_ip", found)
    return {"interface": interface, "verified": True}


def step_bridge(ctx: Context) -> dict[str, Any]:
    """C - stock to recovery bridge.  First write; no rollback below this line."""
    plug_ip, version, vendor_port = ctx.state.require(
        "plug_ip", "stock_version", "vendor_port"
    )
    listen_ip = ctx.listen_ip()
    email = ctx.state.input("email") or ask("eWeLink email:")
    ctx.state.set_input("email", email)
    env = ctx.ewelink_env()

    section("First firmware install - read this one", [
        "Everything so far has been reversible. This step is not.",
        "",
        "It replaces the plug's Sonoff firmware with our own. If the new",
        "firmware fails to start - which has not happened in testing, but can -",
        "the plug cannot be recovered over Wi-Fi. Getting it back would mean",
        "opening the case and connecting a USB-serial adapter to it.",
        "",
        "Before answering:",
        "  - the plug is in a socket that will not be switched off",
        "  - nothing else is about to interrupt your Wi-Fi",
        "  - you are willing to lose this plug if it goes wrong",
        "",
        "You will be asked for your eWeLink password next. It is used to tell",
        "the plug to start its update, and is not saved anywhere.",
    ])
    if not ask_yes_no(
        "Go ahead and install",
        help_lines=["Answer n to stop. Nothing has been written to the plug yet."],
        default=False,
    ):
        raise Abort("not confirmed")

    lock = ROOT / "RECOVERY_LOCK"
    moved = ROOT / "RECOVERY_LOCK.owner-authorized"
    started = time.monotonic()
    lock.rename(moved)
    try:
        code = run_tool(
            [sys.executable, "tools/serve_tasmota_ota.py",
             "--listen-ip", listen_ip, "--listen-port", vendor_port,
             "--device-ip", plug_ip,
             "--firmware", "artifacts/s60-ota-bridge-v3-1.2.1.ota",
             "--email", email, "--expected-current-version", version,
             "--mdns-capture", str(ctx.dir / "mdns.json"),
             "--i-understand-stock-has-no-automatic-rollback"],
            interactive=True,
            env=env,
        )
    finally:
        moved.rename(lock)
        say("RECOVERY_LOCK restored")
    if code != 0:
        raise Abort("bridge delivery failed")
    return {"seconds": round(time.monotonic() - started, 1)}


def step_berry_tasmota(ctx: Context) -> dict[str, Any]:
    """D1/D2 - Berry-capable Tasmota through the bridge AP (operator-assisted)."""
    image = ROOT / "captures/safeboot-recovery-migration/tasmota32c3-bluetooth.bin"
    say("Looking for the bridge's Wi-Fi network...")
    bridge_ssid = find_ssid(BRIDGE_AP_PREFIX) or "S60-OTA-Bridge-XXXX   (the XXXX varies)"
    section("Switch this computer's Wi-Fi to the plug", [
        "The plug is now running our small recovery firmware. It has made its",
        "own Wi-Fi network, and the only way to talk to it is to join that.",
        "",
        "1. In this computer's Wi-Fi menu, join:",
        f"       network   {bridge_ssid}",
        "       password  s60-ota-bridge",
        "2. You will lose internet while joined to it. That is expected.",
        "3. Open a SECOND terminal window, go to this same folder, and run the",
        "   command below. Leave this window alone.",
    ])
    say("")
    say("  curl --http1.1 --silent --show-error --connect-timeout 5 --max-time 300 \\")
    say("    -H 'Expect:' -H 'Content-Type: application/octet-stream' \\")
    say(f"    --data-binary '@{image}' \\")
    say("    http://192.168.4.1/update")
    say("")
    say(f"It sends {image.stat().st_size:,} bytes and takes a few minutes.")
    say("Success looks like:  Upload verified. Rebooting into the new application...")
    say("")
    if not ask_yes_no(
        "Did the upload finish and report success",
        help_lines=[
            "Answer n if it errored, stalled, or you are unsure. Nothing is lost:",
            "the plug still has the recovery firmware and this step can be re-run.",
        ],
        default=False,
    ):
        raise Abort("the firmware upload did not complete")

    say("")
    say("Waiting for the plug to restart and announce itself...")
    device_name = wait_for_ssid(TASMOTA_AP_PREFIX, timeout=120.0)
    if device_name:
        ctx.state.set_input("device_name", device_name)
        naming = [
            f"2. This plug's name is  {device_name}",
            "   That is the name of its setup network, and the same name it will",
            "   show under in your router's list of connected devices - useful if",
            "   you have several plugs and cannot tell them apart.",
        ]
    else:
        device_name = None
        naming = [
            "2. Its network is named  tasmota-XXXXXX-NNNN  where the X's and N's",
            "   differ per plug. The same name appears in your router's device",
            "   list, so note it when you join.",
        ]

    section("Put the plug back on your Wi-Fi", [
        "The plug has rebooted into Tasmota and made its own setup network.",
        "",
        f"1. Join that Wi-Fi network. It is open - there is no password.",
        *naming,
        "3. A setup page should open by itself. If not, browse to",
        "       http://192.168.4.1",
        "4. Choose your normal home Wi-Fi, type its password, and save.",
        "5. Rejoin this computer to your normal Wi-Fi.",
    ])
    if not ask_yes_no("Done - is the plug on your home Wi-Fi now", default=True):
        raise Abort("the plug needs to be on your network before the next step")

    ip = None
    if device_name:
        say(f"Looking up {device_name} on your network...")
        ip = resolve_host(device_name)
        if ip:
            say(f"Found it at {ip} - no need to go hunting in the router.")
            if not ask_yes_no(f"Use {ip}", default=True):
                ip = None
    if not ip:
        looking_for = device_name or "a name beginning tasmota-"
        ip = ask_ipv4(
            "What address is the plug on now",
            help_lines=[
                "Open your router's list of connected devices and find",
                f"  {looking_for}",
                "then read off its address. It is usually different from the one",
                "the plug had as a Sonoff device.",
            ],
            must_respond=True,
        )
    ctx.state.set_input("plug_ip", ip)
    return {"plug_ip": ip, "device_name": device_name}


def step_promote(ctx: Context) -> dict[str, Any]:
    """D4/D5 - move Tasmota to the high slot and freeze NVS."""
    device = ctx.device
    image = ROOT / "captures/safeboot-recovery-migration/tasmota32c3-bluetooth.bin"
    device.wait_reachable(timeout=120)
    slot = device.current_ota()
    say(f"The plug is running from slot {slot}. The migration needs slot 1.")
    if slot == 1:
        say("Nothing to do here.")
        return {"current_ota": slot, "uploads": 0}
    if ctx.dry_run:
        say("[dry-run] would upload the same image through Firmware Upgrade")
        return {"current_ota": slot, "uploads": 0}

    # Every upload writes the other slot and boots it, so this alternates.
    # One swap is expected; a second is allowed in case the run started from an
    # odd position.  Anything beyond that is a real fault, not bad luck.
    uploads = 0
    for attempt in range(2):
        uptime_before = device.uptime_seconds()
        upload_or_prompt(ctx, image, "Berry Tasmota")
        uploads += 1
        say("Waiting for the plug to restart into the other slot...")
        device.wait_for_reboot(timeout=300, before=uptime_before)
        slot = device.current_ota()
        say(f"Now running from slot {slot}.")
        if slot == 1:
            break
        if attempt == 0:
            say("Each upload writes the other slot and boots it, so that swap put")
            say("it back where it started. Doing it once more to land on slot 1.")
    after = slot
    if after != 1:
        raise Abort(
            f"the plug is still running from slot {after} after {uploads} uploads; "
            "it should alternate on every upload, so something is stopping the new "
            "image being selected"
        )
    device.command("SaveData 0")
    say("SaveData 0 - settings frozen so the migration's checks stay stable")
    return {"current_ota": after, "uploads": uploads}


def step_preflight(ctx: Context) -> dict[str, Any]:
    """E1 - read-only inspection.  Re-runnable at any time."""
    code, _ = ctx.phase_runner().run("preflight")
    if code != 0:
        raise Abort(f"preflight did not pass (exit {code})")
    return {"exit": code}


def step_stage(ctx: Context) -> dict[str, Any]:
    """E2 - writes the inactive slot only.  Re-runnable; changes no boot state."""
    code, _ = ctx.phase_runner().run("stage")
    if code != 0:
        raise Abort(f"stage did not pass (exit {code})")
    return {"exit": code}


def step_commit(ctx: Context) -> dict[str, Any]:
    """E3 - the single destructive operation.  Never automatically re-run."""
    section("The point of no return", [
        "Both checks above passed. The next operation rewrites the one small",
        "area of memory that tells the plug where its firmware lives.",
        "",
        "It takes about a minute. If power is lost during it, the plug will not",
        "start again and cannot be fixed over Wi-Fi - only by opening the case.",
        "",
        "Do not unplug it, do not switch the socket off, and do not close this",
        "window until it reports PASS.",
    ])

    def gate(_: str) -> bool:
        return require_phrase(COMMIT_PHRASE)

    lock = ROOT / "REPARTITION_LOCK"
    moved = ROOT / "REPARTITION_LOCK.owner-authorized"
    lock.rename(moved)
    try:
        code, _ = ctx.phase_runner().run(
            "commit",
            extra_flags=["--i-accept-power-loss-may-require-opening-the-plug"],
            confirm=gate,
        )
    finally:
        moved.rename(lock)
        say("REPARTITION_LOCK restored")
    if code != 0:
        raise Abort(f"commit did not pass (exit {code})")
    return {"exit": code}


def step_canonical(ctx: Context) -> dict[str, Any]:
    """F - first canonical boot, then the official application."""
    app = ROOT / "captures/safeboot-recovery-migration/tasmota32c3.bin"
    current = ctx.state.input("plug_ip")
    section("The plug has restarted with its new layout", [
        "It is now running a small recovery firmware whose only job is to accept",
        "the real Tasmota. It joins your Wi-Fi by itself.",
        "",
        f"It may come back on {current}, or take a new address. Check your",
        "router's device list if it does not answer.",
    ])
    # Answering a ping proves nothing here: the old firmware is still up for a
    # second or two after Restart 99.  Wait for the firmware itself to change.
    say("")
    say("Waiting for the plug to restart into the recovery firmware.")
    say("This waits for the firmware to actually change, not just for a ping.")
    time.sleep(10)
    waited = 0.0
    started = time.monotonic()
    while time.monotonic() - started < 300:
        device = ctx.device
        if device.reachable(timeout=3.0):
            try:
                version = device.firmware_version()
            except (DeviceError, OSError):
                version = ""
            if version and "safeboot" in version.lower():
                waited = time.monotonic() - started
                say(f"    Recovery firmware is up at {device.host} "
                    f"({version}, after {waited:.0f}s).")
                break
            if version:
                say(f"    {device.host} is answering, but still running {version} "
                    "- waiting for the restart.")
            else:
                say(f"    {device.host} is answering but not saying what it runs "
                    "- checking its web page instead.")
                if device.is_safeboot():
                    waited = time.monotonic() - started
                    say(f"    Recovery firmware is up at {device.host} "
                        f"(after {waited:.0f}s).")
                    break
        time.sleep(5)
    else:
        choice = ask_choice(
            "The recovery firmware has not appeared. What now",
            [
                "Keep waiting",
                "It is on a different address - let me type it",
                "Stop here",
            ],
            help_lines=[
                "It should restart into a small recovery firmware whose version",
                "contains the word safeboot. If the plug is answering but still",
                "reports the old firmware, the restart has not happened.",
            ],
        )
        if choice == 1:
            ctx.state.set_input("plug_ip", ask_ipv4("What address is it on now", must_respond=True))
        raise Abort("the recovery firmware did not appear; nothing was written")
    device = ctx.device
    if ctx.dry_run:
        say("[dry-run] would upload the official application")
        return {"safeboot_seconds": round(waited, 1)}
    before = device.uptime_seconds()
    how = upload_or_prompt(ctx, app, "Official Tasmota (app0)")

    # The new firmware starts with no Wi-Fi details, because the migration
    # resets the plug's saved settings.  So it may come back on the network, or
    # it may raise its own setup network instead.  Both mean it worked.
    say("")
    say("Waiting for the full Tasmota to start. It will either rejoin your")
    say("Wi-Fi, or put up its own setup network - both are normal.")
    started = time.monotonic()
    outcome = None
    while time.monotonic() - started < 300:
        time.sleep(5)
        if device.reachable(timeout=3.0) and not device.is_safeboot():
            outcome = "rejoined the network"
            say(f"    The plug is back at {device.host} running the full Tasmota.")
            break
        setup_network = find_ssid(TASMOTA_AP_PREFIX, timeout=10)
        if setup_network:
            outcome = f"raised its setup network {setup_network}"
            say(f"    The plug has started and is asking to be set up.")
            say(f"    Its network is called  {setup_network}")
            ctx.state.set_input("device_name", setup_network)
            break
    if outcome is None:
        raise Abort(
            "the plug neither rejoined the network nor put up a setup network "
            "within five minutes"
        )
    return {"safeboot_seconds": round(waited, 1), "app_upload": how, "outcome": outcome}


def step_configure(ctx: Context) -> dict[str, Any]:
    """F3/F4 - Wi-Fi, settings persistence and the S60 template."""
    current = ctx.state.input("plug_ip")
    section("Set up the finished firmware", [
        "The real Tasmota is installed. If it is already on your Wi-Fi there is",
        "nothing to do here. If it is not, it will have made a setup network again:",
        "",
        "  join  tasmota-XXXXXX-NNNN  (open, no password)",
        "  browse to http://192.168.4.1 and choose your home Wi-Fi",
        "",
        "Then rejoin your normal Wi-Fi before answering.",
    ])
    found = wait_or_ask(current, what="the plug", timeout=300.0)
    if found != current:
        ctx.state.set_input("plug_ip", found)
    device = ctx.device
    device.wait_reachable(timeout=300)
    device.command("SaveData 1")
    device.command(f"Template {S60_TEMPLATE}")
    device.command("Module 0")
    say("Template applied; the plug restarts")
    device.wait_for_reboot(timeout=180)
    return {"template": "applied"}


def step_restore(ctx: Context) -> dict[str, Any]:
    """G - replace the private recovery Safeboot with official bytes."""
    runner = ctx.phase_runner()
    rule("Restore official Safeboot")

    def gate(_: str) -> bool:
        section("Swap the temporary startup firmware for the official one", [
            "To get the plug through the rebuild, it was given a small startup",
            "firmware with your Wi-Fi details built into it. That was only ever",
            "meant to be temporary.",
            "",
            "This step replaces it with the official Tasmota version, so the plug",
            "ends up exactly matching the published release.",
            "",
            "This is a safe step. The plug carries on running normally the whole",
            "time, and nothing it needs to start up is touched. If it goes wrong",
            "you can simply run it again.",
        ])
        try:
            matches, problem = verify_app(ctx)
        except (DeviceError, OSError, ValueError) as exc:
            say(f"  Could not check the plug's firmware first: {exc}")
            matches = False
            problem = "the check could not run"
        if matches:
            say("  Checked: the plug is running the official Tasmota, unmodified.")
        else:
            say(f"  Warning: {problem}.")
            say("  That is not what was expected here. Stopping is the safer answer.")
        return ask_yes_no(
            "Go ahead and put the official startup firmware on",
            default=matches,
        )

    code, _ = runner.run(
        "restore", extra_flags=["--i-confirm-normal-app-is-stable"], confirm=gate
    )
    if code != 0:
        raise Abort(f"restore did not pass (exit {code})")
    return {"restore": code}


def step_verify(ctx: Context) -> dict[str, Any]:
    """H1 - the three hashes, read back off the device itself."""
    manifest = load_manifest(ctx.manifest_path)
    artifacts = manifest["artifacts"]
    device = ctx.device
    device.wait_reachable(timeout=180)

    expected = {
        "table": (TABLE_OFFSET, TABLE_SIZE, artifacts["target_table"]["sha256"]),
        "safeboot": (SAFEBOOT_OFFSET, artifacts["safeboot"]["size"], artifacts["safeboot"]["sha256"]),
        "app0": (APP0_OFFSET, artifacts["app"]["size"], artifacts["app"]["sha256"]),
    }
    results: dict[str, Any] = {}
    ok = True
    for name, (address, length, wanted) in expected.items():
        actual = device.sha_region(address, length)
        matched = actual == wanted.lower()
        ok = ok and matched
        results[name] = {"expected": wanted, "actual": actual, "match": matched}
        say(f"  {name:<9} 0x{address:06X} {length:>9,}  {'MATCH' if matched else 'DIFFERS'}")
        if not matched:
            say(f"    expected {wanted}")
            say(f"    actual   {actual}")
    if not ok:
        raise Abort("device does not match the pinned artifacts")
    say("")
    say("All three regions match the pinned official artifacts.")
    return results


def _hash_matches(ctx: "Context", name: str, offset: int, size_key: str) -> tuple[bool, str]:
    manifest = load_manifest(ctx.manifest_path)
    artifact = manifest["artifacts"][size_key]
    actual = ctx.device.sha_region(offset, artifact["size"])
    if actual == artifact["sha256"].lower():
        return True, ""
    return False, f"{name} on the plug no longer matches the pinned image"


def verify_high_slot(ctx: "Context") -> tuple[bool, str]:
    """Was the plug promoted to the high slot?  Only meaningful before the commit.

    After the layout is replaced there is no high slot to be in, and Safeboot has
    no Berry to ask.  Both are 'cannot check', which must not be reported as
    'the device disagrees'.
    """
    if ctx.state.is_done("E3"):
        return True, ""  # superseded: the old two-slot layout no longer exists
    slot = ctx.device.current_ota()
    if slot == 1:
        return True, ""
    if slot is None:
        raise DeviceError(
            "the running slot cannot be read - the plug is not running a "
            "Berry-capable build"
        )
    return False, f"expected the plug to be running from the high slot, it reports {slot}"


def verify_table(ctx: "Context") -> tuple[bool, str]:
    manifest = load_manifest(ctx.manifest_path)
    wanted = manifest["artifacts"]["target_table"]["sha256"].lower()
    actual = ctx.device.sha_region(TABLE_OFFSET, TABLE_SIZE)
    if actual == wanted:
        return True, ""
    return False, "the plug's partition table is not the migrated one"


def verify_app(ctx: "Context") -> tuple[bool, str]:
    return _hash_matches(ctx, "app0", APP0_OFFSET, "app")


def verify_safeboot(ctx: "Context") -> tuple[bool, str]:
    return _hash_matches(ctx, "safeboot", SAFEBOOT_OFFSET, "safeboot")


# Checked before a completed step is skipped on resume.  A step with no entry
# here is skipped on the record alone; these are the ones worth proving.
STEP_VERIFIERS: dict[str, Callable[["Context"], tuple[bool, str]]] = {
    "D4": verify_high_slot,
    "E3": verify_table,
    "F2": verify_app,
    "G1": verify_safeboot,
}


STEPS: list[tuple[str, str, Callable[[Context], dict[str, Any]]]] = [
    ("A5", "Offline preconditions", step_check),
    ("A1", "Pair the plug in eWeLink", step_pair),
    ("A2", "Discover, metadata and device key", step_discover),
    ("B2", "Router DNAT and interception proof", step_intercept),
    ("C2", "Stock to recovery bridge", step_bridge),
    ("D1", "Berry-capable Tasmota via bridge AP", step_berry_tasmota),
    ("D4", "Promote to high slot and freeze NVS", step_promote),
    ("E1", "Preflight (read-only)", step_preflight),
    ("E2", "Stage Safeboot into the spare slot", step_stage),
    ("E3", "Commit the new layout", step_commit),
    ("F2", "Canonical boot and official app", step_canonical),
    ("F4", "Wi-Fi, SaveData and S60 template", step_configure),
    ("G1", "Restore official Safeboot", step_restore),
    ("H1", "Verify the three regions", step_verify),
]
STEP_INDEX = {step: (title, handler) for step, title, handler in STEPS}


# ---------------------------------------------------------------------- CLI


def cmd_status(ctx: Context) -> int:
    say(f"Plug:      {ctx.plug}")
    say(f"Address:   {ctx.state.input('plug_ip') or '(not set)'}")
    say(f"Manifest:  {ctx.manifest_path}")
    say(f"State:     {ctx.state.path}")
    say("")
    say(ctx.state.summary([(step, title) for step, title, _ in STEPS]))
    captures = ctx.state.data.get("captures", {})
    if captures:
        say("")
        say("Captures:")
        for key, value in sorted(captures.items()):
            say(f"  {key}: {json.dumps(value)}")
    return 0


DESTRUCTIVE_STEPS = {"C2", "E2", "E3", "F2", "G1"}


def execute(ctx: Context, step: str) -> int:
    title, handler = STEP_INDEX[step]
    rule(f"{step}  {title}")
    if step in DESTRUCTIVE_STEPS:
        ctx.state.mark(step, ATTEMPTED)
    try:
        captured = handler(ctx)
    except QuitRun:
        say("")
        say(f"Stopped at {step}. Nothing was left half-done; run the same command")
        say("again when you are ready and it will pick up here.")
        return 130
    except (Abort, DeviceError, ValueError) as exc:
        ctx.state.mark(step, FAILED, error=str(exc))
        say(f"FAILED: {exc}")
        return 1
    ctx.state.mark(step, DONE, **{"result": captured})
    for key, value in (captured or {}).items():
        ctx.state.capture(f"{step}.{key}", value)
    say(f"{step} done")
    return 0


def cmd_step(ctx: Context, step: str) -> int:
    if step not in STEP_INDEX:
        say(f"unknown step {step}; known: {', '.join(s for s, _, _ in STEPS)}")
        return 2
    return execute(ctx, step)


# What each step does, in words that assume no knowledge of the internals.
PLAIN_DESCRIPTION = {
    "A5": "checks the files on this computer before anything is touched",
    "A1": "gets the plug onto your network through the eWeLink app",
    "A2": "asks the plug what firmware it is running",
    "B2": "tests that your router is redirecting the plug's update download",
    "C2": "replaces the plug's Sonoff firmware with our small installer firmware",
    "D1": "puts a full Tasmota onto the plug through the installer's own Wi-Fi",
    "D4": "moves Tasmota into the other half of the plug's memory, out of the way",
    "E1": "reads the plug's memory layout and checks it, changing nothing",
    "E2": "copies the new startup firmware into the unused half of the memory",
    "E3": "rewrites the plug's memory map - the one step that cannot be undone",
    "F2": "installs the full Tasmota now that the new layout is in place",
    "F4": "sets up Wi-Fi, saves settings and applies the S60 plug template",
    "G1": "replaces the temporary startup firmware with the official one",
    "H1": "checks the finished plug against the official files",
}


def evidence_bridge_installed(ctx: "Context") -> tuple[bool | None, str]:
    """Only the bridge firmware broadcasts that network."""
    bridge = find_ssid(BRIDGE_AP_PREFIX)
    if bridge:
        return True, (
            f"The plug is broadcasting a Wi-Fi network called '{bridge}'.\n"
            "  Only the installer firmware does that, so this step did finish."
        )
    return None, (
        "No installer network is visible from this computer. That does not mean\n"
        "  it failed - the plug may still be starting up, or this computer's Wi-Fi\n"
        "  may be busy or switched off."
    )


def evidence_official_app_installed(ctx: "Context") -> tuple[bool | None, str]:
    """Safeboot still running means the official firmware never landed."""
    device = ctx.device
    if not device.reachable(timeout=3.0):
        # Not answering may simply mean it is waiting to be set up: the migration
        # clears its Wi-Fi details, so the new firmware comes up asking for them.
        setup_network = find_ssid(TASMOTA_AP_PREFIX)
        if setup_network:
            return True, (
                f"The plug is not on your network, but it is broadcasting a Wi-Fi\n"
                f"  network called '{setup_network}'.\n"
                "  That is the full Tasmota asking to be set up - the temporary\n"
                "  Safeboot firmware cannot do that, so the install did finish.\n"
                "\n"
                "  You will need to join that network, open http://192.168.4.1,\n"
                "  choose your home Wi-Fi and save. The next step walks you\n"
                "  through it, so carrying on is the right answer."
            )
        return None, (
            f"The plug at {device.host} is not answering and no setup network is\n"
            "  visible from here. It may still be starting, or it may have taken a\n"
            "  different address - your router's device list will show it.\n"
            "  Give it a minute and try again."
        )
    if device.is_safeboot():
        return False, (
            "The plug's web page still shows Safeboot.\n"
            "  Safeboot is the small temporary firmware that only knows how to\n"
            "  accept an update - so the full Tasmota is not installed yet, and\n"
            "  this step needs to run again."
        )
    version = device.firmware_version()
    if version:
        return True, (
            f"The plug is running {version}, not Safeboot.\n"
            "  The full Tasmota is installed, so this step did finish."
        )
    return None, (
        "The plug is answering but will not say what it is running, so there is\n"
        "  no way to tell from here."
    )


# Consulted when a step was interrupted, to answer "did it finish?" from the
# device rather than from the operator's memory.
ATTEMPTED_EVIDENCE: dict[str, Callable[["Context"], tuple[bool | None, str]]] = {
    "C2": evidence_bridge_installed,
    "F2": evidence_official_app_installed,
}


def confirm_skip(ctx: Context, step: str, title: str) -> bool:
    """Trust the checklist only as far as the plug agrees with it."""
    verifier = STEP_VERIFIERS.get(step)
    if verifier is None:
        say(f"[skip] {step} {title} (recorded as done)")
        return True
    try:
        ok, message = verifier(ctx)
    except (DeviceError, Abort, OSError, ValueError) as exc:
        say(f"[skip] {step} {title} (recorded as done; could not re-check: {exc})")
        return True
    if ok:
        say(f"[skip] {step} {title} (done, and the plug still agrees)")
        return True
    say("")
    say(f"{step} is recorded as done, but {message}.")
    say("The record and the device disagree, so the checklist cannot be trusted here.")
    return not ask_yes_no(f"Run {step} again", default=False)


def cmd_run(ctx: Context) -> int:
    for step, title, _ in STEPS:
        if ctx.state.is_done(step) and confirm_skip(ctx, step, title):
            continue
        if ctx.state.status(step) == ATTEMPTED:
            section("The last step was interrupted", [
                f"The step that {PLAIN_DESCRIPTION.get(step, title.lower())} was",
                "stopped part way through - a crash, a dropped connection, or a",
                "closed window. Nothing is broken; we just need to know whether it",
                "got far enough before it stopped.",
                "",
                "Doing it again is normally safe. Every step checks the plug before",
                "writing anything and refuses if the plug is not as it expects.",
            ])
            options = [
                "Do that step again",
                "It did finish - move on to the next step",
                "Stop, I want to look at the plug myself",
            ]
            evidence = ATTEMPTED_EVIDENCE.get(step)
            if evidence is not None:
                say("Checking the plug to work out which it is...")
                try:
                    finished, reason = evidence(ctx)
                except (DeviceError, Abort, OSError, ValueError) as exc:
                    finished, reason = None, f"The check itself could not run: {exc}"
                say(f"  {reason}")
                if finished is True:
                    options[1] += "   <- recommended"
                elif finished is False:
                    options[0] += "   <- recommended"
            choice = ask_choice("What would you like to do", options)
            if choice == 1:
                ctx.state.mark(step, DONE, note="operator confirmed it completed")
                say(f"{step} marked done on your say-so.")
                continue
            if choice == 2:
                say("Stopped. Run the same command again when you are ready.")
                return 1
        code = execute(ctx, step)
        if code != 0:
            say("")
            say(f"Stopped at {step}. Fix the cause, then resume with:")
            say("  python3 tools/s60_autoflash.py run")
            return code
    say("")
    say("Conversion complete.")
    return 0


def main() -> int:
    # Keep piping into head/less quiet rather than raising BrokenPipeError.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "status", "step", "list"])
    parser.add_argument("step_id", nargs="?")
    parser.add_argument(
        "--run",
        "--plug",
        dest="run",
        help=argparse.SUPPRESS,  # power-user escape hatch; never needed normally
    )
    parser.add_argument("--new", action="store_true", help="start a fresh run, never resume")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--plug-ip")
    parser.add_argument(
        "--listen-ip",
        help="workstation address the plug fetches from; derived from the plug's "
        "address when omitted",
    )
    parser.add_argument("--email")
    parser.add_argument("--web-user", help="Tasmota web username when the UI has a password")
    parser.add_argument("--web-password", help="Tasmota web password (not stored in run state)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "list":
            args.run = args.run or "-"
        else:
            args.run = resolve_run_name(args.run, args.new, args.plug_ip)
        ctx = Context(args)
    except (Abort, ValueError, OSError) as exc:
        say(f"REFUSING: {exc}")
        return 2

    if args.command == "list":
        directories = run_directories()
        if not directories:
            say("No conversions recorded yet.")
            return 0
        for directory in directories:
            state = RunState.open(directory)
            done = sum(1 for step, _, _ in STEPS if state.is_done(step))
            mark = "verified" if run_is_complete(directory) else "in progress"
            say(f"  {directory.name:<22} {done}/{len(STEPS)} steps  {mark}"
                f"  {state.input('plug_ip') or ''}")
        return 0

    if args.command == "status":
        return cmd_status(ctx)
    if args.command == "step":
        if not args.step_id:
            say("step requires a step id")
            return 2
        return cmd_step(ctx, args.step_id)
    return cmd_run(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
