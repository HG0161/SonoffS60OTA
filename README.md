# Sonoff S60 → Tasmota, without opening the plug

Turns a stock Sonoff S60 smart plug into one running **official Tasmota**, over
Wi-Fi, with no soldering and without taking the plug apart.

One command walks you through it:

```sh
python3 tools/s60_autoflash.py run
```

It asks questions in plain English, checks the plug at every stage, and can be
stopped and restarted at any point without losing its place.

---

## Read this before you start

**This is a mains-voltage device and one step cannot be undone.**

Partway through, the tool rewrites the small area of memory that tells the plug
where its firmware lives. It takes about a minute. If the power is cut during
that minute, the plug will not start again, and no amount of Wi-Fi cleverness
will bring it back — it would need opening and a USB-serial adapter.

That step has worked every time it has been run, and everything before it is
reversible. But please only do this with a plug you can afford to lose.

Two things reduce the risk to nearly nothing:

- plug it into a socket nobody is going to switch off
- don't start if you need the plug working in the next hour

**This is not official Sonoff or Tasmota software.** Converting the plug ends
any support or warranty you had, and the eWeLink app will no longer control it.

---

## What you need

| | |
|---|---|
| **The plug** | A Wi-Fi Sonoff S60 (ESP32-C3 inside). Tested on the UK S60TPG on stock firmware 1.1.1 and 1.2.0. |
| **An eWeLink account** | Free. A throwaway one is fine. The plug has to be added to it first. |
| **A computer** | Linux, with Wi-Fi, Python 3.10 or newer. |
| **Your router's admin page** | You need to add one redirect rule. If you can't get into your router, you can't do this. |
| **About an hour** | Most of it waiting. |

Your Wi-Fi must be **2.4 GHz** — the plug cannot use 5 GHz.

## What it has been run on

| Plug | Model | Stock firmware | What was done |
|---|---|---|---|
| 1 | UK S60TPG | 1.1.1, updated to vendor 1.2.0 first | Converted with wrapped bridge v2, then v3, then Tasmota |
| 2 | UK S60TPG | 1.1.1 | Converted with wrapped bridge v3 directly, then Tasmota |
| 3 | UK S60TPG | 1.1.1 | Converted, then migrated to the official partition layout by hand |
| 4 | UK S60TPG | 1.1.1 | The whole thing by the automated script - conversion and layout |

All four are UK Type G plugs from the `SN-ESP32C3-S60-01` firmware family.
Plugs 3 and 4 end up byte-for-byte identical to a plug flashed over USB with
the official Tasmota release, verified by reading their flash back.

**Reported by others.** [mati1988r](https://github.com/HG0161/SonoffS60OTA/issues/1)
ran the read-only checks — mDNS discovery and the encrypted LAN connection — on
a **European** S60 on stock **1.2.0**, and reported them working. Nobody has yet
reported a completed conversion starting from 1.2.0 using the published
artifacts. If that is your plug, treat the path as unverified, and please say
how you get on.

If you convert one, an issue saying which model, which region and which stock
version is genuinely useful — this table is short because only a handful of
plugs have ever been through it.

## Getting set up

```sh
git clone https://github.com/HG0161/SonoffS60OTA.git
cd SonoffS60OTA
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests
```

The tests should all pass. They check the tool against itself and take a few
seconds; nothing touches the plug.

## Doing it

```sh
python3 tools/s60_autoflash.py run
```

That's the whole interface. It will:

1. **Ask you to add the plug in the eWeLink app** and turn on LAN Control.
2. **Ask the plug what firmware it has** and where it downloads updates from.
3. **Give you one rule to add to your router** — with the exact values filled
   in — then test that the rule works before anything is written.
4. **Replace the Sonoff firmware** with a small installer of ours. *This is the
   point of no return.*
5. **Ask you to join two Wi-Fi networks**, briefly, so the plug can be given
   its real firmware. It tells you the network names.
6. **Rebuild the plug's memory layout** to match the official Tasmota one. It
   shows you what it has checked and asks you to type one sentence to confirm.
7. **Install official Tasmota**, apply the S60 settings, and check the result.

At the end it verifies the finished plug against the official Tasmota release,
region by region, and tells you whether every byte matches.

### If you have to stop

Close the window, lose your Wi-Fi, run out of time — it doesn't matter. Run the
same command again:

```sh
python3 tools/s60_autoflash.py run
```

It remembers which plug it was working on and what it had done. Where a step
was interrupted, it checks the plug to work out whether that step finished
before asking you anything, and tells you what it found.

## What you end up with

Official Tasmota on the official partition layout — the same arrangement you
would get from flashing the plug over USB:

| Area | What's in it | Size |
|---|---|---|
| `nvs` | Settings | 20 KiB |
| `otadata` | Which firmware to start | 8 KiB |
| `safeboot` | Recovery firmware for future updates | 832 KiB |
| `app0` | Tasmota itself | 2,880 KiB |
| `spiffs` | File storage | 320 KiB |

Which means normal Tasmota upgrades from then on, a filesystem the plug never
had, and 2,880 KiB of room for firmware instead of the 1,984 KiB the Sonoff
layout allowed.

After it finishes, check the physical side yourself: the relay clicks, the
button toggles it, the LED follows, and with a load plugged in the voltage,
current and power readings look sensible.

## When something goes wrong

Start with **[docs/troubleshooting.md](docs/troubleshooting.md)** — it covers
the problems that actually came up while this was being built, including what
each one looked like on screen.

The most common by far is the router rule. It is not a normal port forward, and
plenty of routers can't do it or call it something unexpected.

## How it works, roughly

The stock firmware will accept an update if the command comes from the account
that owns the plug. The tool uses that: it asks the plug's own update server
for the firmware details, has your router send that one download to your
computer instead, and answers with our firmware rather than Sonoff's.

From there the plug is ours, and the rest is careful housekeeping — get a full
Tasmota on, move it out of the way, rebuild the memory layout underneath it,
then put the real thing back.

**[docs/what-happens.md](docs/what-happens.md)** explains it properly, in
English, including why the risky step is risky.

## Safety, and why you can believe the checks

- **Nothing is trusted, everything is hashed.** Every firmware file is checked
  against a known SHA-256 before use, and the finished plug is read back and
  compared against the official release.
- **The destructive step is locked by default.** A file in this repository has
  to be deliberately renamed before the tool will even offer it, and it then
  requires a typed sentence — not a y/n.
- **The plug checks too.** Every stage that writes refuses to run unless the
  plug is in exactly the state it expects, and says so rather than guessing.
- **Everything is recorded.** Each run keeps its evidence in `captures/`, which
  git ignores, so nothing private ends up published.

## For developers

The user-facing tool sits on top of a set of smaller, single-purpose scripts in
`tools/`, each of which can be run on its own. `archive/` holds the research
tools, the bridge firmware source and the custom Tasmota build that got the
early plugs converted - none of it is needed now, and none of it is deleted. The research behind all of it —
how the update mechanism was worked out, the partition analysis, the reviewed
migration plan and the manual procedure — is in
**[docs/reference/](docs/reference/)**.

Start with [docs/reference/manual-procedure.md](docs/reference/manual-procedure.md)
if you want to do it by hand, and
[docs/reference/ota-findings.md](docs/reference/ota-findings.md) for how the
firmware format was decoded.

## Thanks

Worked out with the Tasmota community's discussion of this device, and built on
Tasmota itself. Tasmota is GPL-3.0-only; so is this.

See [`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
