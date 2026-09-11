#!/usr/bin/env bash
set -euo pipefail

# irc-send.sh - Send a message to IRC as ag, muse, ci, builder, or card
ENV_NAME="${IRC_ENV:-prod}"

if [[ $# -gt 0 && ("$1" == "--test" || "$1" == "--prod") ]]; then
  if [[ "$1" == "--test" ]]; then
    ENV_NAME="test"
  else
    ENV_NAME="prod"
  fi
  shift
fi

BOT="${1:-ag}"
if [[ "$BOT" == "antigravity" || "$BOT" == "agv" ]]; then
  BOT="ag"
fi
CHANNEL="${2:-#refineid}"
shift 2 || true
MESSAGE="$*"

if [[ -z "$MESSAGE" ]]; then
  MESSAGE=$(cat -)
fi

SOCKET_PATH="/tmp/irc-agent-bridge-${ENV_NAME}.sock"
if [[ ! -S "$SOCKET_PATH" && -S "/tmp/irc-agent-bridge.sock" ]]; then
  SOCKET_PATH="/tmp/irc-agent-bridge.sock"
fi

if [[ -S "$SOCKET_PATH" ]]; then
  python3 -c "
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect('$SOCKET_PATH')
payload = f'$BOT:$CHANNEL:$MESSAGE'
s.sendall(payload.encode('utf-8'))
s.close()
" 2>/dev/null || true
else
  PORT=6697
  USE_TLS="True"
  if [[ "$ENV_NAME" == "test" ]]; then
    PORT=6667
    USE_TLS="False"
  fi
  python3 -c "
import socket, sys, ssl
nick = '$BOT'
if $USE_TLS:
    ctx = ssl.create_default_context()
    s = ctx.wrap_socket(socket.socket(), server_hostname='oc.daemon.fi')
    s.connect(('127.0.0.1', $PORT))
else:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(('127.0.0.1', $PORT))
s.sendall(f'NICK {nick}\r\nUSER {nick} 0 * :Bot\r\nJOIN $CHANNEL\r\nPRIVMSG $CHANNEL :$MESSAGE\r\nQUIT :bye\r\n'.encode('utf-8'))
s.close()
" 2>/dev/null || true
fi
