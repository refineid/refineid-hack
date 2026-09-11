#!/bin/sh
# Verifies every hack without hardware: shell syntax, Swift typecheck,
# C syntax-only compile. Usage: ./check.sh
set -eu
cd "$(dirname "$0")"

fail=0

if command -v sh >/dev/null 2>&1; then
  sh -n fetch_suomi_occupation.sh && echo "sh syntax: ok" || fail=1
fi

if command -v swiftc >/dev/null 2>&1; then
  swiftc -typecheck kcdump.swift -framework Security && echo "swift: ok" || fail=1
else
  echo "swift: skipped (no swiftc)"
fi

if [ "$(uname)" = "Linux" ] && command -v cc >/dev/null 2>&1; then
  cc -fsyntax-only usb_ioctl_helper.c && echo "c: ok" || fail=1
else
  echo "c: skipped (linux-only helper)"
fi

exit "$fail"
