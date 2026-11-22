#!/bin/sh
# Copyright 2026 Petri Koistinen. Licensed under the Apache License, Version 2.0.
#
# One TLS client-certificate request through an OpenSSL pkcs11-provider.
#
# Usage: probe.sh HOST PATH [CERT-URI] [KEY-URI] [PIN-SOURCE]
#
# See README.md. The PIN is never read or printed here: a PIN-SOURCE is
# passed to the provider as part of the PKCS#11 URI, and the provider
# reads the file.

set -eu

usage() {
    echo "usage: $0 HOST PATH [CERT-URI] [KEY-URI] [PIN-SOURCE]" >&2
    exit 2
}

[ "$#" -ge 2 ] || usage

host=$1
path=$2
cert=${3:-pkcs11:type=cert}
key=${4:-pkcs11:type=private}
pin_source=${5:-}

if [ -n "$pin_source" ]; then
    cert="$cert;$pin_source"
    key="$key;$pin_source"
fi

log=$(mktemp)
trap 'rm -f "$log"' EXIT

request=$(printf 'GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: pkcs11-cert-probe\r\nAccept: */*\r\nConnection: close\r\n\r\n' \
    "$path" "$host")

printf '%s' "$request" |
    openssl s_client -tls1_2 -alpn http/1.1 \
        -connect "$host:443" -servername "$host" \
        -state -prexit -ign_eof \
        -cert "$cert" -key "$key" >"$log" 2>&1 || true

status=$(grep -a -o -E 'HTTP/1\.[01] [0-9]{3}' "$log" | head -1)
printf 'status: %s\n' "${status:-none}"

title=$(grep -a -o -E '<title>[^<]*</title>' "$log" | head -1)
[ -n "$title" ] && printf '%s\n' "$title"

grep -a -o -E '>[^<>]{4,70}<' "$log" | head -5 || true

case "$status" in
*' 2'*) exit 0 ;;
esac

echo "no success status; check the reader, the token and the provider log" >&2
exit 1
