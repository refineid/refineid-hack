#!/usr/bin/env bash
set -euo pipefail

# start-irc-review.sh - Manage local test and production IRC review environments

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="$DIR/ngircd.conf"
BRIDGE_SCRIPT="$DIR/irc-agent-bridge.py"

# Production environment configuration (override via env)
PROD_SSH_HOST="${IRC_PROD_SSH_HOST:-production-host}"
PROD_TUNNEL_PORT=6697

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
  echo "         RefineID Multi-Agent IRC Review Environments"
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
  echo -n " [PROD] Tunnel to $PROD_SSH_HOST (127.0.0.1:$PROD_TUNNEL_PORT): "
  if nc -z 127.0.0.1 $PROD_TUNNEL_PORT 2>/dev/null; then
    echo "ONLINE"
  else
    echo "OFFLINE (load: launchctl load ~/Library/LaunchAgents/fi.daemon.oc-irc-tunnel.plist)"
  fi
  echo -n " [PROD] Remote Cloud Daemon on $PROD_SSH_HOST: "
  if ssh -o ConnectTimeout=2 -o BatchMode=yes "$PROD_SSH_HOST" "systemctl is-active --quiet refineid-irc-ag.service" 2>/dev/null; then
    echo "ACTIVE (systemd 24/7 permanent)"
  else
    echo "INACTIVE (ssh $PROD_SSH_HOST 'sudo systemctl start refineid-irc-ag.service')"
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
  echo "        [TEST] RefineID Local Test IRC Review Environment"
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
echo "==> [PROD] Checking SSH tunnel to $PROD_SSH_HOST on 127.0.0.1:$PROD_TUNNEL_PORT..."
if ! nc -z 127.0.0.1 $PROD_TUNNEL_PORT 2>/dev/null; then
  echo "==> Launching tunnel via launchctl..."
  launchctl load ~/Library/LaunchAgents/fi.daemon.oc-irc-tunnel.plist 2>/dev/null || true
  sleep 2
  if ! nc -z 127.0.0.1 $PROD_TUNNEL_PORT 2>/dev/null; then
    echo "==> Fallback: launching manual ssh tunnel..."
    ssh -f -N -L $PROD_TUNNEL_PORT:127.0.0.1:$PROD_TUNNEL_PORT -o ExitOnForwardFailure=yes "$PROD_SSH_HOST"
    sleep 1
  fi
fi

echo "==> [PROD] Verifying permanent cloud daemon on $PROD_SSH_HOST..."
if ssh -o ConnectTimeout=3 -o BatchMode=yes "$PROD_SSH_HOST" "systemctl is-active --quiet refineid-irc-ag.service" 2>/dev/null; then
  echo "==> Remote multi-agent daemon is ACTIVE on $PROD_SSH_HOST (PID $(ssh "$PROD_SSH_HOST" 'systemctl show -p MainPID --value refineid-irc-ag.service 2>/dev/null'))."
else
  echo "==> Starting remote daemon on $PROD_SSH_HOST via systemctl..."
  ssh "$PROD_SSH_HOST" "sudo systemctl restart refineid-irc-ag.service"
fi

echo ""
echo "=================================================================="
echo "      [PROD] RefineID Production IRC Review Environment"
echo "=================================================================="
echo " Environment: PRODUCTION ($PROD_SSH_HOST)"
echo " Server     : $PROD_SSH_HOST:6697 (Let's Encrypt TLSv1.3)"
echo " Local Port : 127.0.0.1:$PROD_TUNNEL_PORT (authenticated tunnel)"
echo " Channel    : #refineid"
echo " Daemon     : refineid-irc-ag.service (24/7 systemd on $PROD_SSH_HOST)"
echo " Server Log : /home/pk/src/refineid-hack/irc/logs/channel-refineid-prod.log"
echo ""
echo " Connect to Production:"
echo "   From Mac    : irssi"
echo "   From Server : ssh -t $PROD_SSH_HOST irssi"
echo "=================================================================="
