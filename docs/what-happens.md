# What actually happens to your plug

Written for someone who wants to understand what they are about to do, without
needing to know anything about embedded firmware. Nothing here is required
reading — the tool works without it — but the one irreversible step is easier
to consent to if you know why it is irreversible.

## The problem

A Sonoff S60 arrives running Sonoff's own firmware, which talks to Sonoff's
servers. Replacing it normally means opening the case and connecting a wire to
the chip inside. That is fiddly, voids everything, and on a mains plug it is
genuinely dangerous if you get it wrong.

There is another way in. The plug already knows how to replace its own
firmware — that is how Sonoff ships updates. It will do it when the owner of
the plug asks. You are the owner.

## Getting in

When the plug updates itself, it asks Sonoff's servers what is available, and
they answer with a download address. The plug then fetches the firmware from
that address and installs it.

The tool intercepts exactly that one download. You add a rule to your router
saying "when this plug tries to reach that particular address, send it to my
computer instead". The plug asks for its update, your computer answers, and the
plug installs what it is given.

This is why the router rule matters so much, and why the tool refuses to go on
until it has proved the rule works. If the redirect silently fails, the plug
just downloads Sonoff's genuine firmware and updates normally — no harm, but no
progress either.

Nothing is faked or bypassed here. The command telling the plug to update is
signed in to your own eWeLink account, and the plug verifies that. It simply
does not care that the file it receives is not Sonoff's.

## Why it takes several stages

You cannot replace everything at once, because the plug is running from the
memory you want to change.

Think of the plug's memory as a shelf with two large slots for firmware. The
plug runs from one and can write to the other. So:

1. Sonoff's firmware is in the first slot. We put a small installer of ours in
   the second, and restart into it.
2. The installer puts a full Tasmota back into the first slot.
3. Tasmota then copies itself into the second slot and restarts, so the entire
   first half of memory is now free to be rebuilt.

Every stage runs from somewhere that is not about to be overwritten. That is
the whole trick, and it is why the process looks like more steps than it
should.

## The step that cannot be undone

At the very start of the plug's memory is a small table — a few thousand bytes
— listing where everything lives. The bootloader reads it every time the plug
powers on. Without it, the plug does not know where its firmware is and will
not start at all.

The Sonoff layout divides memory into two firmware slots and no file storage.
The official Tasmota layout divides it differently: a small recovery firmware,
one much larger area for Tasmota, and space for files. To get the official
layout, that table has to be replaced.

There is only one copy of it. No spare, no backup the bootloader can fall back
on. Replacing it means erasing it and writing the new one — and for the moment
in between, the plug has no valid table. That moment is about a second, inside
a step that takes a minute, and if the power fails during it, the plug is
finished as far as Wi-Fi is concerned.

Everything the tool does around that second is designed to make it as brief and
as certain as possible: the new firmware is already in place and verified before
the table is touched, the replacement table is checked byte for byte in memory
first, and the plug refuses the whole operation unless it is running from the
far end of memory, well away from everything being changed.

## Why your Wi-Fi details disappear

The new layout has a smaller settings area than the old one, in a different
place. The plug's saved settings do not survive that move, so after the rebuild
Tasmota starts up as if it were brand new and asks to be set up again.

That is expected, not a fault. It is why the tool asks you to join the plug's
own setup network twice during the process.

## At the end

The tool reads back three parts of the plug's memory, calculates a fingerprint
of each, and compares them against the official Tasmota release files:

- the memory layout table
- the recovery firmware
- Tasmota itself

If all three match, the plug is byte-for-byte identical to a plug flashed over
USB with the official release. Not equivalent — identical.

From then on it is an ordinary Tasmota device: normal updates through its own
web page, normal integrations, and none of it touching Sonoff's servers.
