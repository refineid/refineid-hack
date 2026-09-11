#!/bin/sh
# Verifies every hack by file class, without hardware: shell syntax for
# .sh (except this file), Swift typecheck for .swift, syntax-only C
# compile for .c on Linux (the helper needs a Linux header).
# Usage: ./check.sh
set -eu
cd "$(dirname "$0")"

fail=0

for script in ./*.sh; do
  [ "$script" = "./check.sh" ] && continue
  sh -n "$script" && echo "sh: $script ok" || fail=1
done

if command -v swiftc >/dev/null 2>&1; then
  for source in ./*.swift; do
    swiftc -typecheck "$source" -framework Security \
      && echo "swift: $source ok" || fail=1
  done
else
  echo "swift: skipped (no swiftc)"
fi

if [ "$(uname)" = "Linux" ] && command -v cc >/dev/null 2>&1; then
  for source in ./*.c; do
    cc -fsyntax-only "$source" && echo "c: $source ok" || fail=1
  done
else
  echo "c: skipped (linux-only helpers)"
fi

exit "$fail"
