#!/usr/bin/env bash
set -euo pipefail

# irc-send.sh - Send a message to local IRC as antigravity or muse
BOT="${1:-antigravity}"
CHANNEL="${2:-#code-review}"
shift 2 || true
MESSAGE="$*"

if [[ -z "$MESSAGE" ]]; then
  MESSAGE=$(cat -)
fi

SOCKET_PATH="/tmp/irc-agent-bridge.sock"

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
  # Fallback to direct raw IRC connection
  python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(('127.0.0.1', 6667))
nick = '$BOT'
s.sendall(f'NICK {nick}\r\nUSER {nick} 0 * :Bot\r\nJOIN $CHANNEL\r\nPRIVMSG $CHANNEL :$MESSAGE\r\nQUIT :bye\r\n'.encode('utf-8'))
s.close()
" 2>/dev/null || true
fi
