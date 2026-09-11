# ReFineID Multi-Agent IRC Review Chatroom

A local, private IRC chatroom where coding agents (`antigravity` and `muse`) collaborate and debate code reviews with the maintainer in real-time.

```
+-------------------------------------------------------------+
|               Local IRC Server (ngIRCd :6667)                |
|                    Channel: #code-review                     |
+------------------------------+------------------------------+
                               |
       +-----------------------+-----------------------+
       |                       |                       |
+------v-------+        +------v-------+        +------v-------+
|  Maintainer  |        | Antigravity  |        |  Muse Code   |
| (petri / pk) |        |    (agv)     |        |   (agent)    |
+--------------+        +--------------+        +--------------+
```

## Quick Start

### 1. Launch Server & Agent Bridge
```bash
./start-irc-review.sh
```
This starts:
- `ngircd` on `127.0.0.1:6667` with `#code-review` and `#refineid` predefined.
- `irc-agent-bridge.py` which connects `antigravity` and `muse` bots to both channels.

### 2. Connect Your IRC Client
Connect from any terminal:
```bash
# irssi
irssi -c 127.0.0.1 -p 6667 -n petri

# weechat
weechat -r "/server add local 127.0.0.1/6667; /connect local; /join #code-review"

# netcat (minimalist)
nc 127.0.0.1 6667
# Then enter:
# NICK petri
# USER petri 0 * :Petri
# JOIN #code-review
```

## How It Works

### 1. Interactive In-Channel Agent Prompts
You can chat with agents directly in `#code-review` or `#refineid`:
- `muse: check if line 295 in site/beta/index.html matches the clone command`
- `antigravity: what are the pre-commit checks in refineid-mono-internal?`

The bridge routes the message to the corresponding agent runtime (`muse exec --yolo` or `agy --print`) and sends the response back to IRC.

### 2. Persistent Multi-Turn PR Reviews
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
Every discussion turn is broadcast live to `#code-review` so you can watch the debate unfold and chime in at any point.

### 3. Symmetrical AGV Reviews for Muse
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
