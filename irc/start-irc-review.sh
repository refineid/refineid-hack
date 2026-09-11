#!/usr/bin/env bash
set -euo pipefail

# start-irc-review.sh - Manage local test and oc.daemon.fi production IRC review environments

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="$DIR/ngircd.conf"
BRIDGE_SCRIPT="$DIR/irc-agent-bridge.py"

MODE="prod"
if [[ $# -gt 0 ]]; then
  case "$1" in
    --test|test|-t)
      MODE="test"
      ;;
    --prod|prod|-p)
      MODE="prod"
      ;;
    status|--status)
      MODE="status"
      ;;
    *)
      echo "Usage: $0 [--prod | --test | status]"
      exit 1
      ;;
  esac
fi

if [[ "$MODE" == "status" ]]; then
  echo "=================================================================="
  echo "         ReFineID Multi-Agent IRC Review Environments"
  echo "=================================================================="
  echo -n " [TEST] Local Server (127.0.0.1:6667) : "
  if nc -z 127.0.0.1 6667 2>/dev/null; then
    echo "ONLINE"
  else
    echo "OFFLINE (start with: $0 --test)"
  fi
  echo -n " [TEST] Agent Bridge (/tmp/irc-agent-bridge-test.sock): "
  if [[ -S "/tmp/irc-agent-bridge-test.sock" ]]; then
    echo "ACTIVE"
  else
    echo "INACTIVE"
  fi

  echo ""
  echo -n " [PROD] Tunnel to oc.daemon.fi (127.0.0.1:6697): "
  if nc -z 127.0.0.1 6697 2>/dev/null; then
    echo "ONLINE"
  else
    echo "OFFLINE (load: launchctl load ~/Library/LaunchAgents/fi.daemon.oc-irc-tunnel.plist)"
  fi
  echo -n " [PROD] Agent Bridge (/tmp/irc-agent-bridge-prod.sock): "
  if [[ -S "/tmp/irc-agent-bridge-prod.sock" ]]; then
    echo "ACTIVE"
  else
    echo "INACTIVE"
  fi
  echo "=================================================================="
  exit 0
fi

if [[ "$MODE" == "test" ]]; then
  echo "==> [TEST] Checking local ngIRCd on 127.0.0.1:6667..."
  if ! nc -z 127.0.0.1 6667 2>/dev/null; then
    echo "==> Starting local test ngIRCd on 127.0.0.1:6667..."
    if ! command -v ngircd >/dev/null 2>&1; then
      echo "Error: ngircd not found. Install via: brew install ngircd" >&2
      exit 1
    fi
    ngircd -f "$CONF_FILE"
    sleep 1
  fi

  echo "==> [TEST] Checking test agent bridge daemon..."
  if [[ ! -S "/tmp/irc-agent-bridge-test.sock" ]] || ! pgrep -f "irc-agent-bridge.py.*--env test" >/dev/null 2>&1; then
    echo "==> Starting test agent bridge daemon..."
    pkill -f "irc-agent-bridge.py.*--env test" 2>/dev/null || true
    rm -f "/tmp/irc-agent-bridge-test.sock"
    nohup /opt/homebrew/bin/python3 "$BRIDGE_SCRIPT" --env test >/tmp/irc-agent-bridge-test.log 2>&1 &
    sleep 1
  fi

  echo ""
  echo "=================================================================="
  echo "        [TEST] ReFineID Local Test IRC Review Environment"
  echo "=================================================================="
  echo " Environment: LOCAL TEST / DEVELOPMENT"
  echo " Server     : 127.0.0.1:6667 (local ngIRCd)"
  echo " Channel    : #refineid"
  echo " Socket     : /tmp/irc-agent-bridge-test.sock"
  echo " Log        : $DIR/logs/channel-refineid-test.log"
  echo ""
  echo " Connect via Irssi:"
  echo "   irssi (then '/connect test' or autoconnect)"
  echo "   nc 127.0.0.1 6667"
  echo "=================================================================="
  exit 0
fi

# Default: PROD
echo "==> [PROD] Checking SSH tunnel to oc.daemon.fi on 127.0.0.1:6697..."
if ! nc -z 127.0.0.1 6697 2>/dev/null; then
  echo "==> Launching tunnel via launchctl..."
  launchctl load ~/Library/LaunchAgents/fi.daemon.oc-irc-tunnel.plist 2>/dev/null || true
  sleep 2
  if ! nc -z 127.0.0.1 6697 2>/dev/null; then
    echo "==> Fallback: launching manual ssh tunnel..."
    ssh -f -N -L 6697:127.0.0.1:6697 -o ExitOnForwardFailure=yes oc
    sleep 1
  fi
fi

echo "==> [PROD] Checking production agent bridge daemon..."
if [[ ! -S "/tmp/irc-agent-bridge-prod.sock" ]] || ! pgrep -f "irc-agent-bridge.py.*--env prod" >/dev/null 2>&1; then
  echo "==> Starting production agent bridge daemon (TLS to oc.daemon.fi)..."
  pkill -f "irc-agent-bridge.py.*--env prod" 2>/dev/null || true
  rm -f "/tmp/irc-agent-bridge-prod.sock"
  nohup /opt/homebrew/bin/python3 "$BRIDGE_SCRIPT" --env prod >/tmp/irc-agent-bridge-prod.log 2>&1 &
  sleep 2
fi

echo ""
echo "=================================================================="
echo "      [PROD] ReFineID Production IRC Review Environment"
echo "=================================================================="
echo " Environment: PRODUCTION (oc.daemon.fi)"
echo " Server     : oc.daemon.fi:6697 (Let's Encrypt TLSv1.3)"
echo " Local Port : 127.0.0.1:6697 (authenticated tunnel)"
echo " Channel    : #refineid"
echo " Socket     : /tmp/irc-agent-bridge-prod.sock"
echo " Log        : $DIR/logs/channel-refineid-prod.log"
echo ""
echo " Connect to Production:"
echo "   From Mac    : irssi"
echo "   From Server : ssh -t oc irssi"
echo "=================================================================="
