#!/bin/sh
set -eu

usage() {
    cat >&2 <<'EOF'
Usage: sudo ./tools/test_ota_firewall.sh INTERFACE PLUG_IP WORKSTATION_IP VENDOR_OTA_IP [PORT]

Run only after creating the source-specific DNAT and MASQUERADE rules.
The S60 must be unplugged and the local OTA server must not be running.

Example:
  sudo ./tools/test_ota_firewall.sh wlo1 192.168.1.50 192.168.1.20 203.0.113.10 8088
EOF
}

if [ "$#" -lt 4 ] || [ "$#" -gt 5 ]; then
    usage
    exit 2
fi

interface=$1
test_source=$2
normal_source=$3
target=$4
port=${5:-8088}

case "$port" in
    ''|*[!0-9]*)
        echo "PORT must be a number." >&2
        exit 2
        ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root." >&2
    exit 2
fi
if ! ip -4 address show dev "$interface" >/dev/null 2>&1; then
    echo "Interface does not exist: $interface" >&2
    exit 2
fi
if ! ip -4 address show dev "$interface" | grep -Fq " $normal_source/"; then
    echo "$normal_source is not assigned to $interface." >&2
    exit 2
fi
if ip -4 address show dev "$interface" | grep -Fq " $test_source/"; then
    echo "$test_source is already assigned locally; refusing to continue." >&2
    exit 2
fi
if ping -c 1 -W 1 "$test_source" >/dev/null 2>&1; then
    echo "$test_source replied. Unplug the S60 and check for an IP conflict." >&2
    exit 2
fi

cleanup() {
    ip address delete "$test_source/32" dev "$interface" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ip address add "$test_source/32" dev "$interface"

echo "Test 1/3: check that plug-source traffic is intercepted"
if curl --silent --output /dev/null \
    --interface "$test_source" --connect-timeout 5 --max-time 8 \
    "http://$target:$port/" 2>/dev/null; then
    echo "FAIL: plug-source traffic reached the vendor endpoint." >&2
    exit 1
else
    echo "PASS: the plug-source request was redirected away from the vendor endpoint."
fi

echo "Test 2/3: check that normal workstation traffic is not intercepted"
if curl --silent --output /dev/null \
    --interface "$normal_source" --connect-timeout 5 --max-time 8 \
    "http://$target:$port/" 2>/dev/null; then
    echo "PASS: normal workstation traffic was not intercepted."
else
    echo "INCONCLUSIVE: normal workstation traffic could not reach the vendor endpoint." >&2
    exit 1
fi

echo "Test 3/3: remove the temporary plug address"
cleanup
echo "PASS: the temporary test address was removed."
echo "OVERALL INTERCEPTION PREFLIGHT: PASS"
echo "Reconnect the S60 when ready."
