#!/bin/sh
set -eu

interface="wlo1"
test_source="192.168.1.96"
normal_source="192.168.1.141"
target="52.57.99.135"
port="8088"

if [ "$(id -u)" -ne 0 ]; then
    echo "This test must run as root." >&2
    exit 2
fi
if ip -4 address show dev "$interface" | grep -q " $test_source/"; then
    echo "$test_source is already assigned; refusing to continue." >&2
    exit 2
fi

cleanup() {
    ip address delete "$test_source/32" dev "$interface" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ip address add "$test_source/32" dev "$interface"

if curl --silent --show-error --output /dev/null \
    --interface "$test_source" --connect-timeout 5 --max-time 8 \
    "http://$target:$port/"; then
    echo "FAIL: temporary S60 source reached the OTA server." >&2
    exit 1
else
    echo "PASS: temporary S60 source was rejected by the firewall."
fi

if curl --silent --show-error --output /dev/null \
    --interface "$normal_source" --connect-timeout 5 --max-time 8 \
    "http://$target:$port/"; then
    echo "PASS: normal workstation source still reached the OTA server."
else
    echo "INCONCLUSIVE: normal workstation source could not reach the OTA server." >&2
    exit 1
fi
