# Reference material

The research and engineering behind the converter. None of this is needed to
convert a plug — start at the [README](../../../README.md) for that — but it is
here so the work can be checked, argued with and built on.

## How the conversion works

- **[manual-procedure.md](manual-procedure.md)** — the whole conversion done by
  hand, command by command. This is what the automated tool drives. Useful if
  you want to understand a step, or do it yourself.
- **[ota-findings.md](ota-findings.md)** — how the stock update mechanism and
  its firmware wrapper were decoded: the header, the three CRCs, the manifest
  digest, and what the updater does and does not enforce.
- **[HOWTO-S60TPG-OTA-TASMOTA.md](HOWTO-S60TPG-OTA-TASMOTA.md)** — the complete
  technical procedure in full detail.
- **[AI-GENERATED-GUIDE.md](AI-GENERATED-GUIDE.md)** — an earlier narrative
  walkthrough, kept for background.

## The memory layout work

- **[s60-actual-partition-map.md](s60-actual-partition-map.md)** — the stock
  partition table as decoded from a real flash dump, with its hashes.
- **[part-2-safeboot-migration-plan.md](part-2-safeboot-migration-plan.md)** —
  the reviewed design for replacing that table with the official Tasmota one:
  the phases, what each guards against, and the go/no-go conditions.
- **[part-2-safeboot-migration-runbook.md](part-2-safeboot-migration-runbook.md)**
  — the operator procedure for running it by hand.
- **[part-2-safeboot-recovery-contingency.md](part-2-safeboot-recovery-contingency.md)**
  — the temporary startup firmware with Wi-Fi built in, why a source patch is
  needed for it, and how it is built and later replaced.
- **[part-2-repartitioning.md](part-2-repartitioning.md)** — an earlier design
  that a full flash dump later disproved. **Superseded — do not flash it.**
  Kept because the reasoning is instructive.

## The devices

- **[device-baseline.md](device-baseline.md)** — what was recorded from the
  test plugs before anything was changed.
- **[recovery-1.1.1.md](recovery-1.1.1.md)** — serial recovery, for the one
  failure Wi-Fi cannot fix.

## Publishing

- **[GITHUB-DISCUSSION-POST.md](GITHUB-DISCUSSION-POST.md)** — the write-up
  prepared for the Tasmota community discussion.

## A note on the numbers

Every firmware image used is pinned by SHA-256, and the official Tasmota files
are taken from a fixed commit of the Tasmota install repository rather than a
moving release URL. The finished plug is read back and compared against those
same files, so "it worked" means the bytes match, not that nothing complained.
