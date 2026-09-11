#!/usr/bin/env bash
set -euo pipefail

# discuss-with-agv.sh - Stateful multi-turn code review discussion with Antigravity (agy / agv)

PROGNAME="discuss-with-agv"
AGY_BIN="${AGY_BIN:-/Users/pk/.local/bin/agy}"
CACHE_DIR="${HOME}/.cache/agv-muse-discussions"
mkdir -p "$CACHE_DIR"

notify_irc() {
  local bot="$1"
  local channel="$2"
  local msg="$3"
  if command -v irc-send >/dev/null 2>&1; then
    irc-send "$bot" "$channel" "$msg" 2>/dev/null || true
  fi
}

print_usage() {
  echo "Usage: $PROGNAME <COMMAND> [OPTIONS]"
  echo ""
  echo "Commands:"
  echo "  start --pr <NUM> [--repo <OWNER/REPO>] [--intent <MSG>]"
  echo "      Start a persistent review discussion with Antigravity on a pull request."
  echo ""
  echo "  reply --pr <NUM> [--repo <OWNER/REPO>] <MESSAGE> | --file <PATH>"
  echo "      Send a follow-up reply, explanation, or code update to Antigravity."
  echo ""
  echo "  transcript --pr <NUM> [--repo <OWNER/REPO>]"
  echo "      Display the complete multi-turn discussion transcript."
  echo ""
  echo "  submit --pr <NUM> [--repo <OWNER/REPO>] [--approve | --comment]"
  echo "      Submit the discussion transcript to the GitHub PR."
  echo ""
  echo "Examples:"
  echo "  $PROGNAME start --pr 65 --repo refineid/refineid-mono-internal"
  echo "  $PROGNAME reply --pr 65 \"Fixed the clone/cd mismatch in site/beta/index.html.\""
  echo "  $PROGNAME transcript --pr 65"
  exit 1
}

if [[ $# -eq 0 ]]; then
  print_usage
fi

COMMAND="$1"
shift

PR_NUM=""
REPO=""
INTENT=""
MESSAGE=""
MSG_FILE=""
SUBMIT_EVENT="COMMENT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pr)
      PR_NUM="$2"
      shift 2
      ;;
    --repo)
      REPO="$2"
      shift 2
      ;;
    --intent)
      INTENT="$2"
      shift 2
      ;;
    --file)
      MSG_FILE="$2"
      shift 2
      ;;
    --approve)
      SUBMIT_EVENT="APPROVE"
      shift
      ;;
    --comment)
      SUBMIT_EVENT="COMMENT"
      shift
      ;;
    -h|--help)
      print_usage
      ;;
    *)
      if [[ -z "$MESSAGE" ]]; then
        MESSAGE="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2
        print_usage
      fi
      ;;
  esac
done

if [[ -z "$PR_NUM" ]]; then
  echo "Error: --pr <NUM> is required" >&2
  exit 1
fi

if [[ -z "$REPO" ]]; then
  REPO=$(git config --get remote.origin.url 2>/dev/null | sed -E 's/.*github\.com[:\/]([^\/]+\/[^\/\.]+).*/\1/' || true)
  if [[ -z "$REPO" ]]; then
    echo "Error: Could not auto-detect GitHub repo; please pass --repo <OWNER/REPO>" >&2
    exit 1
  fi
fi

SLUG=$(echo "agv_${REPO}_pr_${PR_NUM}" | tr '/:' '_')
STATE_FILE="${CACHE_DIR}/${SLUG}.json"
TRANSCRIPT_FILE="${CACHE_DIR}/${SLUG}_transcript.md"

case "$COMMAND" in
  start)
    SESSION_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
    echo "==> Initializing persistent discussion session ($SESSION_ID) for $REPO #$PR_NUM with Antigravity..." >&2

    TMP_DIR=$(mktemp -d -t discuss-agv-XXXXXX)
    trap 'rm -rf "$TMP_DIR"' EXIT

    echo "==> Fetching PR metadata and diff..." >&2
    gh pr view "$PR_NUM" --repo "$REPO" > "$TMP_DIR/context.txt"
    gh pr diff "$PR_NUM" --repo "$REPO" > "$TMP_DIR/diff.patch"

    notify_irc muse "#refineid" "--- Review Discussion Started for $REPO PR #$PR_NUM (Session: $SESSION_ID) ---"
    if [[ -n "$INTENT" ]]; then
      notify_irc muse "#refineid" "Author intent: $INTENT"
    fi

    cat << 'EOF' > "$TMP_DIR/prompt.md"
You are Antigravity acting as the senior code reviewer in an iterative, back-and-forth review dialogue with the author/Muse agent.
Review the pull request changes below and audit compliance with all mandatory project rules:

### Mandatory Project Quality & Security Rules:
1. **Character Encoding & Special Symbols**:
   - Source files and project prose may use the ISO-8859-15 character repertoire, including meaningful specification symbols such as `§` and `€`.
   - Never degrade specification symbols to ASCII. Rust source files must be valid UTF-8. Protocol fixtures must preserve their exact specified byte encodings.
2. **Zero PIN and PIN-Length Logging**:
   - STRICT ZERO PIN LOGGING across all environments: Never log, trace, display, or format PIN bytes, candidate PIN lengths (e.g. `got {len}`), or development PIN identifiers in log sinks, audit records, or error strings.
   - Never commit test PINs or card secrets.
3. **Memory Safety & Language Boundaries**:
   - Safe Rust must own protocol parsing, secret handling, and state machines.
   - Keep `unsafe` code strictly inside the PC/SC or Windows Card Module boundary.
   - Every Windows ABI pointer access must validate nullability and buffer length before dereferencing.
   - Every slice or index access must be checked for bounds or use safe iterators/patterns.
4. **Clean Code & Conventions**:
   - Use named constants instead of naked protocol or status values.
   - Verify external claims from Microsoft, DVV, ICAO, eIDAS, or primary sources.
   - Zero AI attribution in git commits, commit messages, or PR bodies.

### Collaboration & Discussion Protocol:
This is a multi-turn conversation. You do not just give a final one-way verdict:
- State your specific observations, questions, edge case concerns, and recommendations.
- Highlight anything that needs clarification, justification, or a follow-up commit.
- End your response with clear questions or required action items for the author.
EOF

    {
      if [[ -n "$INTENT" ]]; then
        echo ""
        echo "### Author's Statement of Intent:"
        echo "$INTENT"
      fi
      echo ""
      echo "### Pull Request Context:"
      cat "$TMP_DIR/context.txt"
      echo ""
      echo "### Git Diff:"
      cat "$TMP_DIR/diff.patch"
    } >> "$TMP_DIR/prompt.md"

    echo "==> Sending initial review request to Antigravity..." >&2
    "$AGY_BIN" --dangerously-skip-permissions --print "$(cat "$TMP_DIR/prompt.md")" > "$TMP_DIR/agv_reply.txt" 2>&1 || {
      echo "Error: agy --print failed" >&2
      cat "$TMP_DIR/agv_reply.txt" >&2
      exit 1
    }

    cat << EOF > "$STATE_FILE"
{
  "repo": "$REPO",
  "pr": "$PR_NUM",
  "session_id": "$SESSION_ID",
  "turns": 1,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

    cat << EOF > "$TRANSCRIPT_FILE"
# PR #$PR_NUM Review Discussion: $REPO
*Session ID: $SESSION_ID*
*Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)*

## Turn 1 (Author/Muse -> Antigravity)
${INTENT:-"Submitted PR #$PR_NUM and diff for rigorous review against ReFineID project rules."}

## Turn 1 (Antigravity Reviewer)
$(cat "$TMP_DIR/agv_reply.txt")

EOF

    notify_irc antigravity "#refineid" "$(cat "$TMP_DIR/agv_reply.txt")"

    cat "$TMP_DIR/agv_reply.txt"
    echo ""
    echo "==> Session saved ($SESSION_ID). To reply to Antigravity, run:" >&2
    echo "    $PROGNAME reply --pr $PR_NUM --repo $REPO \"Your reply or explanation here\"" >&2
    ;;

  reply)
    if [[ ! -f "$STATE_FILE" ]]; then
      echo "Error: No active discussion session found for $REPO PR #$PR_NUM. Run '$PROGNAME start --pr $PR_NUM' first." >&2
      exit 1
    fi

    TURNS=$(jq -r .turns "$STATE_FILE")
    NEXT_TURN=$((TURNS + 1))

    CONTENT=""
    if [[ -n "$MSG_FILE" && -f "$MSG_FILE" ]]; then
      CONTENT=$(cat "$MSG_FILE")
    elif [[ -n "$MESSAGE" ]]; then
      CONTENT="$MESSAGE"
    else
      echo "Error: Specify a message string or --file <PATH>" >&2
      exit 1
    fi

    TMP_DIR=$(mktemp -d -t discuss-agv-reply-XXXXXX)
    trap 'rm -rf "$TMP_DIR"' EXIT

    notify_irc muse "#refineid" "[Turn $NEXT_TURN to Antigravity]: $CONTENT"

    echo "==> Sending Turn $NEXT_TURN to Antigravity..." >&2
    cat << EOF > "$TMP_DIR/reply_prompt.md"
Below is the ongoing review discussion transcript for $REPO PR #$PR_NUM:

$(cat "$TRANSCRIPT_FILE")

---
### New message from Author / Muse (Turn $NEXT_TURN):
$CONTENT

Please review this response / updated status in the context of the full review history. Does this satisfy your questions and findings?
Provide your assessment, any remaining observations, or your updated review status.
EOF

    "$AGY_BIN" --dangerously-skip-permissions --print "$(cat "$TMP_DIR/reply_prompt.md")" > "$TMP_DIR/agv_reply.txt" 2>&1 || {
      echo "Error: agy --print failed" >&2
      cat "$TMP_DIR/agv_reply.txt" >&2
      exit 1
    }

    jq --arg turns "$NEXT_TURN" '.turns = ($turns | tonumber)' "$STATE_FILE" > "$TMP_DIR/updated_state.json"
    mv "$TMP_DIR/updated_state.json" "$STATE_FILE"

    cat << EOF >> "$TRANSCRIPT_FILE"
---
## Turn $NEXT_TURN (Author/Muse -> Antigravity)
$CONTENT

## Turn $NEXT_TURN (Antigravity Reviewer)
$(cat "$TMP_DIR/agv_reply.txt")

EOF

    notify_irc antigravity "#refineid" "[Turn $NEXT_TURN from Antigravity]: $(cat "$TMP_DIR/agv_reply.txt")"

    cat "$TMP_DIR/agv_reply.txt"
    ;;

  transcript)
    if [[ -f "$TRANSCRIPT_FILE" ]]; then
      cat "$TRANSCRIPT_FILE"
    else
      echo "Error: No transcript found for $REPO PR #$PR_NUM" >&2
      exit 1
    fi
    ;;

  submit)
    if [[ ! -f "$TRANSCRIPT_FILE" ]]; then
      echo "Error: No transcript found for $REPO PR #$PR_NUM" >&2
      exit 1
    fi

    echo "==> Posting discussion transcript to GitHub PR #$PR_NUM ($SUBMIT_EVENT)..." >&2
    case "$SUBMIT_EVENT" in
      APPROVE)
        gh pr review "$PR_NUM" --repo "$REPO" --approve --body-file "$TRANSCRIPT_FILE"
        ;;
      *)
        gh pr review "$PR_NUM" --repo "$REPO" --comment --body-file "$TRANSCRIPT_FILE"
        ;;
    esac
    notify_irc muse "#refineid" "Discussion transcript posted to GitHub PR #$PR_NUM ($SUBMIT_EVENT)."
    echo "==> Transcript posted successfully to GitHub PR #$PR_NUM!" >&2
    ;;

  *)
    print_usage
    ;;
esac
