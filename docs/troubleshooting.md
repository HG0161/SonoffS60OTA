# When it goes wrong

Every problem listed here actually happened while this was being built. If you
hit something not covered, the run's own record is in
`captures/<name>/autoflash-state.json` and the phase evidence beside it.

First thing to try, always:

```sh
python3 tools/s60_autoflash.py run
```

It picks up where it stopped. Re-running is safe — every step checks the plug
before writing anything.

---

## The router rule

**"the redirect test failed"**

This is the most common failure by a distance, and it stops the run before
anything is written to the plug, which is exactly what you want.

The rule you need is *not* a normal port forward. A port forward sends traffic
arriving from the internet to a machine inside your network. You need the
opposite: traffic from one device inside your network, heading out, sent to
your computer instead. Routers call this **destination NAT**, **DNAT**, or
sometimes hide it under Port Forwarding as an option for local traffic. On
OpenWrt it is Network → Firewall → Port Forwards.

Things to check:

- The **source** is the plug's address, and the **destination** is the Sonoff
  address the tool printed. Getting those the wrong way round is easy.
- Your router may need **NAT reflection**, **hairpin NAT** or **masquerading**
  turned on as well. Without it the reply reaches the plug from the wrong
  address and the plug ignores it.
- The plug must be **unplugged** during the test — it borrows the plug's
  address for a few seconds.

If your router genuinely cannot do this, you cannot convert the plug this way.

## The plug can't be found

**"did not answer within 180s"**

- Check it is actually powered on. This has caught everyone at least once.
- Its address may have changed. After the rebuild the plug loses its Wi-Fi
  settings and takes a new address when you set it up again. Look in your
  router's device list for a name starting `tasmota-`.
- Reserve the plug's address in your router so it stops moving.

**"Empty reply from server" when you try `curl` yourself**

Tasmota refuses commands that arrive without a `Referer` header, as a security
measure, and refuses by closing the connection — which looks exactly like the
device being switched off. Add the header:

```sh
curl -H "Referer: http://192.168.1.50/" "http://192.168.1.50/cm?cmnd=Status%2011"
```

The tool already does this.

## Uploading firmware

**The upload finishes suspiciously fast and nothing changes**

A firmware upload to this plug takes tens of seconds. If it "succeeds" in under
ten, it did not write anything. Tasmota needs the file's size passed in the
request, and silently commits nothing without it. The tool sends it; if you are
uploading by hand, use the plug's own web page rather than a bare `curl`.

**"the plug rejected the firmware"**

The message includes what the plug said. Usually it means the file is wrong —
check `sha256sum -c SHA256SUMS` passes and that you are uploading the file the
tool named, not the `.factory.bin`.

## Slots and restarts

**"the plug is still running from slot 0 after 2 uploads"**

Each upload writes the *other* half of memory and restarts into it, so the slot
alternates every time. The tool handles one swap-back automatically. If it
reports this, uploads are not being committed at all — see the "suspiciously
fast" entry above.

**A step says it was interrupted**

The tool marks a step before it starts writing, so an interrupted run is
visible rather than silent. It will check the plug and tell you whether the
step got far enough, then let you choose. Take the recommendation unless you
know better.

## After the rebuild

**The plug is broadcasting its own Wi-Fi network**

Expected. The rebuild resets the plug's settings, so Tasmota starts up asking
to be configured. Join the `tasmota-XXXXXX-NNNN` network — it is open, no
password — go to `http://192.168.4.1`, choose your Wi-Fi and save.

**Safeboot is showing instead of Tasmota**

Safeboot is the small recovery firmware. Seeing it after the rebuild is normal;
seeing it after the whole run has finished is not. It means the full Tasmota
was never installed — re-run and let the tool redo that step.

Note that Safeboot has no Wi-Fi setup of its own. It relies on settings already
saved on the plug. That is why the conversion uses a temporary version with
Wi-Fi details built in, and swaps it for the official one at the very end.

## The worst case

**Power was lost while the memory layout was being rewritten**

If the plug does not come back at all after that step, Wi-Fi recovery is not
possible. The plug would need opening and a USB-serial adapter connected to the
chip inside. See
[reference/recovery-1.1.1.md](../archive/docs/reference/recovery-1.1.1.md).

This is the risk you accepted at the start, and it is the only failure in this
process that cannot be fixed by running the tool again.

## Checking a finished plug at any time

```sh
python3 tools/s60_autoflash.py step H1 --plug-ip 192.168.1.50
```

Reads three areas of the plug's memory and compares them against the official
Tasmota files. Read-only — safe to run whenever you want reassurance.
