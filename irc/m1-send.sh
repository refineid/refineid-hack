#!/usr/bin/env bash
set -euo pipefail

# m1-send.sh - Relay a message from m1.local into #refineid
SOCK="/tmp/m1-irc-agent.sock"

if [[ ! -S "$SOCK" ]]; then
  echo "Error: m1 agent socket not found at $SOCK" >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  echo "Usage: $0 <message>" >&2
  exit 1
fi

MSG="$*"
echo "m1:#refineid:$MSG" | nc -U "$SOCK"
