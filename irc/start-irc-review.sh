#!/usr/bin/env bash
set -euo pipefail

# start-irc-review.sh - Start local IRC server and multi-agent review bridge

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="$DIR/ngircd.conf"
BRIDGE_SCRIPT="$DIR/irc-agent-bridge.py"

echo "==> Checking ngIRCd status..."
if ! nc -z 127.0.0.1 6667 2>/dev/null; then
  echo "==> Starting ngIRCd on 127.0.0.1:6667..."
  if ! command -v ngircd >/dev/null 2>&1; then
    echo "Error: ngircd not found. Install via: brew install ngircd" >&2
    exit 1
  fi
  ngircd -f "$CONF_FILE"
  sleep 1
fi

echo "==> Checking IRC agent bridge daemon..."
if [[ ! -S "/tmp/irc-agent-bridge.sock" ]]; then
  echo "==> Starting IRC agent bridge daemon (antigravity & muse)..."
  nohup python3 "$BRIDGE_SCRIPT" >/tmp/irc-agent-bridge.log 2>&1 &
  sleep 1
fi

echo ""
echo "=================================================================="
echo "           ReFineID Multi-Agent IRC Review Chatroom"
echo "=================================================================="
echo " Server   : 127.0.0.1:6667 (ngIRCd)"
echo " Channels : #refineid    (live PR reviews #refineid (live PR reviews & debates) debates)"
echo " Agents   : antigravity (Google Antigravity)"
echo "            muse        (Muse Code Agent)"
echo " Socket   : /tmp/irc-agent-bridge.sock"
echo ""
echo " Join from any terminal using:"
echo "   irssi -c 127.0.0.1 -p 6667 -n petri"
echo "   weechat -r '/server add local 127.0.0.1/6667; /connect local; /join #refineid'"
echo "   nc 127.0.0.1 6667"
echo ""
echo " In-channel commands:"
echo "   muse: <query>         Ask Muse to check code or answer questions"
echo "   antigravity: <query>  Ask Antigravity to check code or answer questions"
echo "=================================================================="
