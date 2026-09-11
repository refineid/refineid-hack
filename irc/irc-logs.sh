#!/usr/bin/env bash
set -euo pipefail

# irc-logs.sh - View and search ReFineID IRC channel chat logs (#refineid)

LOG_ENV="prod"
if [[ $# -gt 0 ]]; then
  case "$1" in
    --test|test|-t)
      LOG_ENV="test"
      shift
      ;;
    --prod|prod)
      LOG_ENV="prod"
      shift
      ;;
  esac
fi

LOG_FILE="/Users/pk/src/refineid-hack/irc/logs/channel-refineid-${LOG_ENV}.log"
ALT_LOG="/Users/pk/src/refineid-hack/irc/logs/channel-refineid.log"
SYMLINK_LOG="/tmp/irc-channel-refineid.log"

if [[ ! -f "$LOG_FILE" && -f "$ALT_LOG" ]]; then
  LOG_FILE="$ALT_LOG"
elif [[ ! -f "$LOG_FILE" && -f "$SYMLINK_LOG" ]]; then
  LOG_FILE="$SYMLINK_LOG"
fi

usage() {
  cat <<EOF
Usage: irc-logs [--test | --prod] [options] [lines]

View and search ReFineID IRC chatroom logs (#refineid).

Environments:
  --prod             View production logs from oc.daemon.fi (default)
  --test             View local test environment logs

Options:
  -n, --lines <N>    Number of lines to view (default: 50)
  -f, --follow       Follow the log in real time (tail -f)
  -s, --search <str> Search the chat log for a string or regex
  -a, --all          Display the entire chat log
  -p, --path         Print the absolute path to the log file
  -h, --help         Show this help message

Examples:
  irc-logs           # View last 50 lines of production log
  irc-logs --test    # View last 50 lines of test log
  irc-logs -f        # Tail production chat in real time
  irc-logs --test -f # Tail test chat in real time
EOF
  exit 0
}

LINES=50
FOLLOW=0
SEARCH=""
SHOW_ALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      ;;
    -p|--path)
      echo "$LOG_FILE"
      exit 0
      ;;
    -f|--follow)
      FOLLOW=1
      shift
      ;;
    -a|--all)
      SHOW_ALL=1
      shift
      ;;
    -n|--lines)
      LINES="$2"
      shift 2
      ;;
    -s|--search)
      SEARCH="$2"
      shift 2
      ;;
    [0-9]*)
      LINES="$1"
      shift
      ;;
    *)
      SEARCH="$1"
      shift
      ;;
  esac
done

if [[ ! -f "$LOG_FILE" ]]; then
  echo "Chat log does not exist yet ($LOG_FILE)."
  echo "Log will be created automatically once messages are sent to #refineid."
  exit 0
fi

if [[ $FOLLOW -eq 1 ]]; then
  tail -n "$LINES" -f "$LOG_FILE"
elif [[ -n "$SEARCH" ]]; then
  grep -i --color=auto "$SEARCH" "$LOG_FILE" || echo "No matches found for: $SEARCH"
elif [[ $SHOW_ALL -eq 1 ]]; then
  cat "$LOG_FILE"
else
  tail -n "$LINES" "$LOG_FILE"
fi
