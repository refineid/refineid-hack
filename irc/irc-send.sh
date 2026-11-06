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

# BOT and CHANNEL become IRC command parameters on both delivery paths, so a
# CR or LF in either would start a new protocol line.
for field in "$BOT" "$CHANNEL"; do
  if [[ "$field" == *$'\r'* || "$field" == *$'\n'* ]]; then
    echo "irc-send: bot and channel must not contain CR or LF" >&2
    exit 2
  fi
done

SOCKET_PATH="/tmp/irc-agent-bridge-${ENV_NAME}.sock"
if [[ ! -S "$SOCKET_PATH" && -S "/tmp/irc-agent-bridge.sock" ]]; then
  SOCKET_PATH="/tmp/irc-agent-bridge.sock"
fi

# The Python programs below are fixed single-quoted text; every value reaches
# them through sys.argv and is handled as bytes, never as source code.
if [[ -S "$SOCKET_PATH" ]]; then
  # The bridge splits the payload at the first two colons and sends each
  # non-empty line of the message as its own PRIVMSG.
  python3 -c '
import os, socket, sys
path, bot, channel, message = sys.argv[1:5]
payload = b":".join(os.fsencode(v) for v in (bot, channel, message))
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(path)
s.sendall(payload)
s.close()
' "$SOCKET_PATH" "$BOT" "$CHANNEL" "$MESSAGE" 2>/dev/null || true
else
  PORT=6697
  USE_TLS="tls"
  if [[ "$ENV_NAME" == "test" ]]; then
    PORT=6667
    USE_TLS="plain"
  fi
  # Each non-empty line of the message is sent as its own PRIVMSG, matching
  # the bridge, so CR or LF in the message cannot inject other IRC commands.
  python3 -c '
import os, re, socket, ssl, sys
use_tls = sys.argv[1] == "tls"
port = int(sys.argv[2])
nick, channel, message = (os.fsencode(v) for v in sys.argv[3:6])
out = b"NICK " + nick + b"\r\nUSER " + nick + b" 0 * :Bot\r\nJOIN " + channel + b"\r\n"
for line in re.split(rb"\r\n|\r|\n", message):
    if line:
        out += b"PRIVMSG " + channel + b" :" + line + b"\r\n"
out += b"QUIT :bye\r\n"
if use_tls:
    ctx = ssl.create_default_context()
    s = ctx.wrap_socket(socket.socket(), server_hostname="oc.daemon.fi")
    s.connect(("127.0.0.1", port))
else:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", port))
s.sendall(out)
s.close()
' "$USE_TLS" "$PORT" "$BOT" "$CHANNEL" "$MESSAGE" 2>/dev/null || true
fi
