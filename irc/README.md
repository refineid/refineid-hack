# ReFineID Multi-Agent IRC Review Chatroom

A local, private IRC chatroom where coding agents (`ag`, `muse`) and specialized service bots (`ci`, `builder`, `card`) collaborate with the maintainer in real-time.

```
+-------------------------------------------------------------------------------+
|                        Local IRC Server (ngIRCd :6667)                        |
|                              Channel: #refineid                               |
+---------------------------------------+---------------------------------------+
                                        |
       +-------------------+------------+------------+-------------------+
       |                   |                         |                   |
+------v-------+    +------v-------+          +------v-------+    +------v-------+
|  Maintainer  |    |  AI Agents   |          | Service Bots |    | Hardware     |
| (petri / pk) |    |  ag / muse   |          |  ci / builder|    |    card      |
+--------------+    +--------------+          +--------------+    +--------------+
```

## Quick Start

### 1. Launch Server & Agent Bridge
```bash
./start-irc-review.sh
```
This starts:
- `ngircd` on `127.0.0.1:6667` with single channel `#refineid` (`Autojoin = yes`).
- `irc-agent-bridge.py` connecting AI agents (`ag`, `muse`) and service bots (`ci`, `builder`, `card`) to `#refineid`.

### 2. Connect Your IRC Client
Connect from any terminal:
```bash
# irssi (preconfigured for direct zero-noise startup into #refineid)
irssi

# weechat
weechat -r "/server add local 127.0.0.1/6667; /connect local; /join #refineid"

# netcat (minimalist)
nc 127.0.0.1 6667
```

## How It Works

### 1. Interactive In-Channel Agent Prompts
You can chat with agents directly in `#refineid`:
- `ag: what are the pre-commit checks in refineid-mono-internal?`
- `muse: check if line 295 in site/beta/index.html matches the clone command`

The bridge routes the message to the corresponding agent runtime (`agy --print` or `muse exec --yolo`) and sends the response back to IRC.

### 2. Specialized Service Bots
The channel includes dedicated service bots that respond instantly to maintenance commands:

- **`ci` (GitHub & CI Bot)**:
  - `ci prs` or `ci prs <repo>`: List open PRs across `refineid` or in a specific repo
  - `ci runs [repo]`: Show latest GitHub Actions workflow run statuses
  - `ci check <pr#> [repo]`: Inspect check run passes/failures for a specific PR
  - `ci view <pr#> [repo]`: View PR summary, review decision, and branch details
  - `ci repos`: List all active `refineid` repositories

- **`builder` (Build & Verifier Bot)**:
  - `builder status`: Quick summary of git branches and uncommitted changes across all local `refineid-*` repos
  - `builder check [repo]`: Run formatting, clippy, or `./Scripts/verify-commit.sh` on a local repo
  - `builder test [repo]`: Run test suites on a local repo (`cargo test`, etc.)
  - `builder diff [repo]`: Show short git diffstat

- **`card` (Hardware & Smart Card Monitor)**:
  - `card status`: Report connected PC/SC smart card readers and inserted cards
  - `card readers`: List all enumerated reader hardware
  - `card atr`: Display Answer-To-Reset metadata for inserted smart cards
  - `card rules`: Display AGENTS.md hardware verification requirements

### 3. Persistent Chat Logging & Agent Reading
All chats and events in `#refineid` are logged to:
- `irc/logs/channel-refineid.log` (and symlinked at `/tmp/irc-channel-refineid.log`).

Agents and maintainer can inspect the chat history anytime:
```bash
irc-logs          # View last 50 lines
irc-logs 20       # View last 20 lines
irc-logs -f       # Follow live chat in real time
irc-logs -s "PR"  # Search chat history
```
When an agent is addressed (`ag:` or `muse:`), recent chat context is automatically supplied to the agent.

### 4. Persistent Multi-Turn PR Reviews
When reviewing or proposing pull requests, start an iterative review discussion:
```bash
# Start a review session with Muse
./discuss-with-muse.sh start --pr 65 --repo refineid/refineid-mono-internal

# Reply to Muse's findings in the same session
./discuss-with-muse.sh reply --pr 65 "Fixed cd path to refineid-unix in commit 87b3aaa."

# View the full transcript
./discuss-with-muse.sh transcript --pr 65

# Submit final approved review to GitHub
./discuss-with-muse.sh submit --pr 65 --approve
```
Every discussion turn is broadcast live to `#refineid` so you can watch the debate unfold and chime in at any point.

### 5. Symmetrical AGV Reviews for Muse
When Muse performs work, it can engage Antigravity in an identical multi-turn discussion:
```bash
./discuss-with-agv.sh start --pr <NUM>
./discuss-with-agv.sh reply --pr <NUM> "Updated implementation"
```

## Enforced Review Rules
Both agents strictly enforce the ReFineID governing rules:
- **ISO-8859-15 encoding**: Preserve meaningful specification symbols like `§` and `€`; never degrade to ASCII.
- **Zero PIN logging**: Zero logging, tracing, or formatting of PIN bytes, lengths, or test secrets across all environments.
- **Safe Rust boundaries**: Safe Rust owns protocol, parsing, and secrets; `unsafe` is strictly confined to PC/SC or Card Module boundaries.
- **Windows ABI validation**: Buffer nullability and length must be validated before pointer dereference.
- **Zero AI attribution**: No AI attribution in commit messages, trailers, or PR bodies.
