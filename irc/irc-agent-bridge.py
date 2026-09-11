#!/usr/bin/env python3
"""
irc-agent-bridge.py - Persistent local IRC multi-agent and service bot bridge.
Connects AI coding agents and service bots to 127.0.0.1:6667 (#refineid):
  Coding Agents:
    - ag       (Google Antigravity Agent, agy --dangerously-skip-permissions --print)
    - muse     (Muse Code Agent, muse exec --yolo)
  Service Bots:
    - ci       (GitHub & CI Bot: PR tracking, workflow runs, check statuses)
    - builder  (Build & Verifier Bot: local git status, formatting, cargo/gradle tests)
    - card     (Hardware & Smart Card Monitor: PC/SC reader and token state)

Logs all channel messages and events to irc/logs/channel-refineid.log for agents to read.
"""

import asyncio
import ctypes
import datetime
import glob
import json
import os
import sys
import subprocess
import shutil

SERVER = "127.0.0.1"
PORT = 6667
CHANNELS = ["#refineid"]
SOCKET_PATH = "/tmp/irc-agent-bridge.sock"
DAEMON_LOG_FILE = "/tmp/irc-agent-bridge.log"

IRC_DIR = os.path.dirname(os.path.abspath(__file__))
CHAT_LOG_DIR = os.path.join(IRC_DIR, "logs")
CHAT_LOG_FILE = os.path.join(CHAT_LOG_DIR, "channel-refineid.log")
SYMLINK_LOG_FILE = "/tmp/irc-channel-refineid.log"

MUSE_BIN = os.environ.get("MUSE_BIN", "/Users/pk/.local/bin/muse")
AGY_BIN = os.environ.get("AGY_BIN", "/Users/pk/.local/bin/agy")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/Users/pk/src")

ALL_BOT_NICKS = ("ag", "antigravity", "agv", "muse", "ci", "gh", "builder", "build", "check", "card", "pcsc")


def ensure_log_dir():
    os.makedirs(CHAT_LOG_DIR, exist_ok=True)
    gitignore = os.path.join(CHAT_LOG_DIR, ".gitignore")
    if not os.path.exists(gitignore):
        try:
            with open(gitignore, "w", encoding="utf-8") as f:
                f.write("*.log\n")
        except Exception:
            pass


def log_daemon(msg):
    line = f"[bridge] {msg}\n"
    sys.stderr.write(line)
    sys.stderr.flush()
    try:
        with open(DAEMON_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def log_chat(entry):
    """Log formatted chat message to persistent log file and symlink."""
    ensure_log_dir()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{now_str}] {entry}\n"

    try:
        with open(CHAT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted)
            f.flush()
    except Exception as e:
        log_daemon(f"Error writing chat log: {e}")

    try:
        if not os.path.islink(SYMLINK_LOG_FILE) and not os.path.exists(SYMLINK_LOG_FILE):
            os.symlink(CHAT_LOG_FILE, SYMLINK_LOG_FILE)
    except Exception:
        pass


def get_recent_chat_context(limit=20):
    """Retrieve last N lines from the chat log for agent context."""
    if not os.path.exists(CHAT_LOG_FILE):
        return ""
    try:
        with open(CHAT_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-limit:]).strip()
    except Exception:
        return ""


def resolve_repo_name(arg):
    """Normalize repo name e.g. windows -> refineid/refineid-windows."""
    if not arg:
        return "refineid/refineid-mono-internal"
    arg = arg.strip().rstrip("/")
    if "/" in arg:
        return arg
    if not arg.startswith("refineid-"):
        arg = f"refineid-{arg}"
    return f"refineid/{arg}"


def resolve_local_repo_dir(arg):
    """Normalize repo path e.g. windows -> /Users/pk/src/refineid-windows."""
    if not arg:
        return os.path.join(WORKSPACE_DIR, "refineid-windows")
    arg = arg.strip().rstrip("/")
    if os.path.isabs(arg) and os.path.isdir(arg):
        return arg
    if not arg.startswith("refineid-"):
        cand = f"refineid-{arg}"
    else:
        cand = arg
    p = os.path.join(WORKSPACE_DIR, cand)
    if os.path.isdir(p):
        return p
    return os.path.join(WORKSPACE_DIR, arg)


# ----------------------------------------------------------------------
# Service Bot Handlers
# ----------------------------------------------------------------------

async def handle_ci_query(sender, query):
    """Service bot \x27ci\x27: GitHub PRs, workflow runs, and check runs."""
    q = query.strip()
    tokens = q.split()
    cmd = tokens[0].lower() if tokens else "help"
    args = tokens[1:]

    if cmd in ("help", "--help", "-h"):
        return (
            "CI & GitHub Bot Commands:\n"
            "  ci prs [repo]          - List open PRs across refineid (or for a specific repo)\n"
            "  ci runs [repo]         - Show latest GitHub Actions workflow runs\n"
            "  ci check <pr#> [repo]  - Inspect check run statuses for a PR\n"
            "  ci view <pr#> [repo]   - Show summary & review state for a PR\n"
            "  ci repos               - List active refineid repositories"
        )

    elif cmd in ("prs", "pr"):
        repo_arg = args[0] if args else None
        if repo_arg:
            full_repo = resolve_repo_name(repo_arg)
            proc = await asyncio.create_subprocess_exec(
                "gh", "pr", "list", "--repo", full_repo, "--state", "open", "--limit", "6",
                "--json", "number,title,author,headRefName",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return f"Error querying PRs: {stderr.decode().strip()}"
            items = json.loads(stdout.decode())
            if not items:
                return f"No open PRs in {full_repo}."
            lines = [f"Open PRs in {full_repo}:"]
            for it in items:
                num = it.get("number")
                title = it.get("title", "")[:50]
                branch = it.get("headRefName", "")
                author = it.get("author", {}).get("login", "")
                lines.append(f"  #{num}: {title} ({branch}, by {author})")
            return "\n".join(lines)
        else:
            proc = await asyncio.create_subprocess_exec(
                "gh", "search", "prs", "--owner", "refineid", "--state", "open", "--limit", "8",
                "--json", "number,title,repository,author",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return f"Error searching PRs: {stderr.decode().strip()}"
            items = json.loads(stdout.decode())
            if not items:
                return "No open PRs across refineid repositories."
            lines = ["Open PRs across refineid:"]
            for it in items:
                r = it.get("repository", {}).get("name", "")
                num = it.get("number")
                title = it.get("title", "")[:50]
                author = it.get("author", {}).get("login", "")
                lines.append(f"  #{num} [{r}]: {title} (by {author})")
            return "\n".join(lines)

    elif cmd in ("runs", "run"):
        repo_arg = args[0] if args else "mono-internal"
        full_repo = resolve_repo_name(repo_arg)
        proc = await asyncio.create_subprocess_exec(
            "gh", "run", "list", "--repo", full_repo, "--limit", "5",
            "--json", "databaseId,name,status,conclusion,headBranch,createdAt",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"Error querying runs: {stderr.decode().strip()}"
        items = json.loads(stdout.decode())
        if not items:
            return f"No recent workflow runs in {full_repo}."
        lines = [f"Recent CI runs for {full_repo}:"]
        for it in items:
            rid = it.get("databaseId")
            name = it.get("name", "")[:30]
            status = it.get("status")
            concl = it.get("conclusion") or status
            branch = it.get("headBranch", "")
            lines.append(f"  run #{rid}: {name} [{concl}] on {branch}")
        return "\n".join(lines)

    elif cmd in ("check", "checks"):
        if not args:
            return "Usage: ci check <pr-number> [repo]"
        pr_num = args[0]
        repo_arg = args[1] if len(args) > 1 else "mono-internal"
        full_repo = resolve_repo_name(repo_arg)
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "checks", pr_num, "--repo", full_repo,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        out_text = stdout.decode().strip()
        if proc.returncode != 0 and not out_text:
            return f"Error checking PR #{pr_num}: {stderr.decode().strip()}"
        if not out_text:
            return f"No checks found for {full_repo} PR #{pr_num}."
        lines = [f"Checks for {full_repo} PR #{pr_num}:"]
        for l in out_text.splitlines()[:6]:
            parts = [p.strip() for p in l.split("\t") if p.strip()]
            if len(parts) >= 2:
                lines.append(f"  {parts[0]}: {parts[1]}")
            else:
                lines.append(f"  {l[:70]}")
        return "\n".join(lines)

    elif cmd in ("view", "info"):
        if not args:
            return "Usage: ci view <pr-number> [repo]"
        pr_num = args[0]
        repo_arg = args[1] if len(args) > 1 else "mono-internal"
        full_repo = resolve_repo_name(repo_arg)
        proc = await asyncio.create_subprocess_exec(
            "gh", "pr", "view", pr_num, "--repo", full_repo,
            "--json", "number,title,author,state,headRefName,baseRefName,reviewDecision,url",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"Error viewing PR: {stderr.decode().strip()}"
        it = json.loads(stdout.decode())
        title = it.get("title")
        author = it.get("author", {}).get("login")
        state = it.get("state")
        branch = it.get("headRefName")
        rev = it.get("reviewDecision") or "NONE"
        url = it.get("url")
        return (
            f"PR #{pr_num} in {full_repo}:\n"
            f"  Title: {title}\n"
            f"  Author: {author} | State: {state} | Review: {rev}\n"
            f"  Branch: {branch} -> {it.get('baseRefName')}\n"
            f"  URL: {url}"
        )

    elif cmd in ("repos", "repositories"):
        proc = await asyncio.create_subprocess_exec(
            "gh", "repo", "list", "refineid", "--limit", "15", "--json", "name,description",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"Error listing repos: {stderr.decode().strip()}"
        items = json.loads(stdout.decode())
        lines = [f"RefineID Repositories ({len(items)}):"]
        for it in items:
            n = it.get("name")
            d = it.get("description") or ""
            lines.append(f"  {n}: {d[:50]}")
        return "\n".join(lines)

    else:
        return f"Unknown command: \x27{cmd}\x27. Type \x27ci help\x27 for available commands."


async def handle_builder_query(sender, query):
    """Service bot \x27builder\x27: Local builds, tests, formatting, git status."""
    q = query.strip()
    tokens = q.split()
    cmd = tokens[0].lower() if tokens else "status"
    args = tokens[1:]

    if cmd in ("help", "--help", "-h"):
        return (
            "Build & Verifier Bot Commands:\n"
            "  builder status         - Check git branch & uncommitted status across all local repos\n"
            "  builder check [repo]   - Run formatting & pre-commit checks on local repo\n"
            "  builder test [repo]    - Run tests on local repo (e.g. cargo test / gradle)\n"
            "  builder diff [repo]    - Show git diff stat for local repo"
        )

    elif cmd in ("status", "st"):
        lines = ["Local Repositories Working Tree Status:"]
        for p in sorted(glob.glob(os.path.join(WORKSPACE_DIR, "refineid-*"))):
            if not os.path.isdir(os.path.join(p, ".git")):
                continue
            rname = os.path.basename(p)
            b_proc = await asyncio.create_subprocess_exec(
                "git", "-C", p, "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            b_out, _ = await b_proc.communicate()
            branch = b_out.decode().strip() or "detached"

            s_proc = await asyncio.create_subprocess_exec(
                "git", "-C", p, "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            s_out, _ = await s_proc.communicate()
            mods = len([l for l in s_out.decode().splitlines() if l.strip()])
            if mods > 0:
                lines.append(f"  {rname} [{branch}]: {mods} uncommitted file(s)")
            else:
                lines.append(f"  {rname} [{branch}]: clean")
        return "\n".join(lines)

    elif cmd in ("diff", "diffstat"):
        repo_arg = args[0] if args else "windows"
        target_dir = resolve_local_repo_dir(repo_arg)
        if not os.path.isdir(target_dir):
            return f"Directory not found: {target_dir}"
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", target_dir, "diff", "--stat",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        out = stdout.decode().strip()
        if not out:
            return f"No unstaged diff in {os.path.basename(target_dir)}."
        return f"Diff stat for {os.path.basename(target_dir)}:\n{out}"

    elif cmd in ("check", "verify", "fmt"):
        repo_arg = args[0] if args else "windows"
        target_dir = resolve_local_repo_dir(repo_arg)
        rname = os.path.basename(target_dir)
        if not os.path.isdir(target_dir):
            return f"Directory not found: {target_dir}"

        # 1. Check if repo has verify-commit.sh
        verify_sh = os.path.join(target_dir, "Scripts", "verify-commit.sh")
        if os.path.isfile(verify_sh) and os.access(verify_sh, os.X_OK):
            proc = await asyncio.create_subprocess_exec(
                verify_sh, cwd=target_dir,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return f"{rname}: verify-commit.sh PASSED clean."
            else:
                err = stderr.decode().strip() or stdout.decode().strip()
                return f"{rname}: verify-commit.sh FAILED:\n{err[:200]}"

        # 2. Check if repo is Rust (Cargo.toml)
        if os.path.isfile(os.path.join(target_dir, "Cargo.toml")):
            # cargo fmt check
            proc1 = await asyncio.create_subprocess_exec(
                "cargo", "fmt", "--check", cwd=target_dir,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, err1 = await proc1.communicate()
            if proc1.returncode != 0:
                return f"{rname}: cargo fmt check FAILED:\n{err1.decode()[:180]}"

            # cargo clippy
            proc2 = await asyncio.create_subprocess_exec(
                "cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings",
                cwd=target_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, err2 = await proc2.communicate()
            if proc2.returncode != 0:
                return f"{rname}: cargo clippy FAILED:\n{err2.decode()[:180]}"

            return f"{rname}: Formatting and clippy checks PASSED clean."

        return f"{rname}: No standard verification harness found."

    elif cmd in ("test", "tests"):
        repo_arg = args[0] if args else "windows"
        target_dir = resolve_local_repo_dir(repo_arg)
        rname = os.path.basename(target_dir)
        if not os.path.isdir(target_dir):
            return f"Directory not found: {target_dir}"

        if os.path.isfile(os.path.join(target_dir, "Cargo.toml")):
            proc = await asyncio.create_subprocess_exec(
                "cargo", "test", "--workspace", cwd=target_dir,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            out = stdout.decode()
            if proc.returncode == 0:
                summary_lines = [l for l in out.splitlines() if "test result:" in l]
                summary = "; ".join(summary_lines) if summary_lines else "all tests passed"
                return f"{rname}: cargo test PASSED ({summary})."
            else:
                err = [l for l in out.splitlines() if "FAILED" in l or "error" in l.lower()]
                return f"{rname}: cargo test FAILED:\n" + "\n".join(err[:3])

        return f"{rname}: Unsupported test framework for automated test run."

    else:
        return f"Unknown command: \x27{cmd}\x27. Type \x27builder help\x27 for available commands."


async def handle_card_query(sender, query):
    """Service bot \x27card\x27: Smart card readers, inserted tokens, ATR status."""
    q = query.strip().lower()
    tokens = q.split()
    cmd = tokens[0] if tokens else "status"

    if cmd in ("help", "--help", "-h"):
        return (
            "Smart Card & Hardware Monitor Commands:\n"
            "  card status     - Report connected PC/SC readers and inserted tokens\n"
            "  card readers    - List all PC/SC card readers enumerated by macOS PCSC\n"
            "  card atr        - Display card Answer-to-Reset (ATR) metadata (zero-PIN safe)\n"
            "  card rules      - Display AGENTS.md hardware verification requirements"
        )

    elif cmd in ("rules", "rule", "gate"):
        return (
            "Hardware Verification Rules (AGENTS.md):\n"
            "  - Zero PIN and PIN-length logging across all environments.\n"
            "  - Safe Rust owns protocol, parsing, and secret handling.\n"
            "  - Hardware claims additionally require a real reader and card.\n"
            "  - Do not publish unsigned or test-signed binaries as production releases."
        )

    # 1. Enumerate PC/SC readers via macOS PCSC.framework
    readers = []
    try:
        pcsc = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/PCSC.framework/PCSC")
        hContext = ctypes.c_void_p()
        if pcsc.SCardEstablishContext(0, None, None, ctypes.byref(hContext)) == 0:
            pcch = ctypes.c_uint32()
            if pcsc.SCardListReaders(hContext, None, None, ctypes.byref(pcch)) == 0 and pcch.value > 1:
                buf = ctypes.create_string_buffer(pcch.value)
                if pcsc.SCardListReaders(hContext, None, buf, ctypes.byref(pcch)) == 0:
                    readers = [r.decode("utf-8", errors="replace") for r in buf.raw.split(b"\x00") if r]
            pcsc.SCardReleaseContext(hContext)
    except Exception as e:
        readers = [f"PCSC error: {e}"]

    # 2. Enumerate smartcards via security list-smartcards
    cards = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "security", "list-smartcards",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        cards = [l.strip() for l in stdout.decode().splitlines() if l.strip()]
    except Exception:
        pass

    if cmd in ("readers", "reader"):
        if readers:
            return f"Detected PC/SC Readers ({len(readers)}):\n  " + "\n  ".join(readers)
        return "No PC/SC card readers currently connected."

    elif cmd in ("atr", "card", "token"):
        if not cards:
            return "No smartcard detected. Insert card into reader."
        return f"Detected Smart Cards ({len(cards)}):\n  " + "\n  ".join(cards)

    else:
        # Default: status
        lines = ["Smart Card & Hardware Reader Status:"]
        if readers:
            lines.append(f"  PC/SC Readers ({len(readers)}): " + ", ".join(readers))
        else:
            lines.append("  PC/SC Readers: None detected")

        if cards:
            lines.append(f"  Inserted Cards ({len(cards)}): " + ", ".join(cards))
        else:
            lines.append("  Inserted Cards: None detected")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# IRC Bot Class
# ----------------------------------------------------------------------

class IrcBot:
    def __init__(self, nick, realname, triggers=None, handler=None, is_logger=False):
        self.nick = nick
        self.realname = realname
        self.triggers = triggers or [f"{nick}:", f"{nick} "]
        self.custom_handler = handler
        self.is_logger = is_logger
        self.reader = None
        self.writer = None
        self.running = True

    async def connect(self):
        while self.running:
            try:
                log_daemon(f"[{self.nick}] Connecting to {SERVER}:{PORT}...")
                self.reader, self.writer = await asyncio.open_connection(SERVER, PORT)
                self.send(f"NICK {self.nick}")
                self.send(f"USER {self.nick} 0 * :{self.realname}")
                for ch in CHANNELS:
                    self.send(f"JOIN {ch}")
                log_daemon(f"[{self.nick}] Connected and joined {CHANNELS}")
                await self.listen_loop()
            except Exception as e:
                log_daemon(f"[{self.nick}] Connection error: {e}, reconnecting in 3s...")
                await asyncio.sleep(3)

    def send(self, line):
        if self.writer and not self.writer.is_closing():
            self.writer.write((line + "\r\n").encode("utf-8"))

    async def privmsg(self, target, msg):
        for line in msg.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            # Log bot response to chat log
            log_chat(f"<{self.nick}> {line}")

            # IRC max line length ~512 bytes
            while len(line.encode("utf-8")) > 400:
                chunk = line[:350]
                line = line[350:]
                self.send(f"PRIVMSG {target} :{chunk}")
                await asyncio.sleep(0.1)
            self.send(f"PRIVMSG {target} :{line}")
            await asyncio.sleep(0.08)

    async def listen_loop(self):
        while self.running:
            line = await self.reader.readline()
            if not line:
                log_daemon(f"[{self.nick}] Connection closed by remote")
                break
            raw = line.decode("utf-8", errors="replace").strip()
            if raw.startswith("PING "):
                token = raw[5:]
                self.send(f"PONG {token}")
                continue

            parts = raw.split(" ", 3)
            if len(parts) < 2:
                continue

            command = parts[1]

            if command == "PRIVMSG" and len(parts) >= 4:
                sender = parts[0][1:].split("!", 1)[0]
                channel = parts[2]
                text = parts[3][1:] if parts[3].startswith(":") else parts[3]

                # Only the designated logger logs incoming channel chat to prevent duplicates
                if self.is_logger and channel.startswith("#"):
                    if sender not in ALL_BOT_NICKS:
                        log_chat(f"<{sender}> {text}")

                # Ignore triggers from our own bots to avoid infinite loops
                if sender in ALL_BOT_NICKS:
                    continue

                await self.handle_message(sender, channel, text)

            elif self.is_logger:
                # Log membership and channel state changes
                sender = parts[0][1:].split("!", 1)[0] if parts[0].startswith(":") else ""
                if command == "JOIN" and len(parts) >= 3:
                    chan = parts[2][1:] if parts[2].startswith(":") else parts[2]
                    log_chat(f"* {sender} joined {chan}")
                elif command == "PART" and len(parts) >= 3:
                    chan = parts[2]
                    reason = parts[3][1:] if len(parts) > 3 and parts[3].startswith(":") else ""
                    log_chat(f"* {sender} left {chan}" + (f" ({reason})" if reason else ""))
                elif command == "QUIT":
                    reason = parts[2][1:] if len(parts) > 2 and parts[2].startswith(":") else ""
                    log_chat(f"* {sender} quit" + (f" ({reason})" if reason else ""))
                elif command == "TOPIC" and len(parts) >= 4:
                    chan = parts[2]
                    topic = parts[3][1:] if parts[3].startswith(":") else parts[3]
                    log_chat(f"* {sender} changed topic of {chan} to: {topic}")

    async def handle_message(self, sender, channel, text):
        target = channel if channel.startswith("#") else sender

        matched = False
        query = ""
        lower_text = text.lower()
        for trig in self.triggers:
            if lower_text.startswith(trig):
                query = text[len(trig):].strip()
                matched = True
                break

        if matched:
            log_daemon(f"[{self.nick}] Triggered by {sender} in {target}: {query}")
            if self.custom_handler:
                # Custom service bot handler
                reply = await self.custom_handler(sender, query)
                await self.privmsg(target, f"{sender}: {reply}")
            else:
                # General AI coding agent dispatch (ag or muse)
                await self.privmsg(target, f"{sender}: Working on it (no limits: {self.nick})...")
                asyncio.create_task(self.dispatch_agent(sender, target, query))

    async def dispatch_agent(self, sender, target, query):
        try:
            recent_context = get_recent_chat_context(15)
            if recent_context:
                full_prompt = (
                    f"Context from recent IRC chat in #refineid:\n"
                    f"---\n{recent_context}\n---\n\n"
                    f"Direct request from {sender}: {query}"
                )
            else:
                full_prompt = query

            if self.nick == "muse":
                cmd = [MUSE_BIN, "exec", "--yolo", full_prompt]
            else:
                cmd = [AGY_BIN, "--dangerously-skip-permissions", "--print", full_prompt]

            log_daemon(f"[{self.nick}] Executing: {' '.join(cmd)}")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=WORKSPACE_DIR
            )
            stdout, stderr = await proc.communicate()
            out_text = stdout.decode("utf-8", errors="replace")
            err_text = stderr.decode("utf-8", errors="replace")

            if self.nick == "muse":
                lines = [l for l in out_text.splitlines() if not l.startswith("muse: workspace")]
                reply = "\n".join(lines).strip()
            else:
                reply = out_text.strip()

            if not reply and err_text:
                reply = f"stderr: {err_text.strip()}"
            elif not reply:
                reply = "(done, no output)"

            log_daemon(f"[{self.nick}] Finished query for {sender}, reply length: {len(reply)} chars")
            await self.privmsg(target, f"{sender}: {reply}")
        except Exception as e:
            log_daemon(f"[{self.nick}] Error executing agent query: {e}")
            await self.privmsg(target, f"{sender}: Error executing agent: {e}")


async def handle_unix_client(reader, writer, bots):
    data = await reader.read(65536)
    writer.close()
    await writer.wait_closed()

    # Protocol: BOT_NAME:CHANNEL:MESSAGE
    decoded = data.decode("utf-8", errors="replace").strip()
    if not decoded:
        return
    parts = decoded.split(":", 2)
    if len(parts) != 3:
        return
    bot_name, channel, message = parts[0].strip().lower(), parts[1].strip(), parts[2]
    target_bot = bots.get(bot_name)
    if target_bot:
        log_daemon(f"[socket] Relaying message from {bot_name} to {channel}")
        await target_bot.privmsg(channel, message)


async def main():
    ensure_log_dir()
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    # 1. AI Coding Agents
    ag_bot = IrcBot(
        "ag", "Google Antigravity Agent",
        triggers=["ag:", "ag,", "ag ", "@ag ", "antigravity:", "agv:"],
        is_logger=True
    )
    muse_bot = IrcBot(
        "muse", "Muse Code Agent",
        triggers=["muse:", "muse,", "muse ", "@muse "]
    )

    # 2. Service Bots
    ci_bot = IrcBot(
        "ci", "ReFineID CI & GitHub Bot",
        triggers=["ci:", "ci,", "ci ", "@ci ", "gh:", "gh "],
        handler=handle_ci_query
    )
    builder_bot = IrcBot(
        "builder", "ReFineID Build & Verifier Bot",
        triggers=["builder:", "builder,", "builder ", "build:", "build ", "check:", "check "],
        handler=handle_builder_query
    )
    card_bot = IrcBot(
        "card", "ReFineID Smart Card Monitor",
        triggers=["card:", "card,", "card ", "@card ", "pcsc:", "pcsc "],
        handler=handle_card_query
    )

    bots = {
        "ag": ag_bot,
        "antigravity": ag_bot,
        "agv": ag_bot,
        "muse": muse_bot,
        "ci": ci_bot,
        "gh": ci_bot,
        "builder": builder_bot,
        "build": builder_bot,
        "check": builder_bot,
        "card": card_bot,
        "pcsc": card_bot,
    }

    server = await asyncio.start_unix_server(
        lambda r, w: handle_unix_client(r, w, bots),
        SOCKET_PATH
    )
    os.chmod(SOCKET_PATH, 0o777)
    log_daemon(f"IRC Agent Bridge running. Socket at {SOCKET_PATH}, Chat log at {CHAT_LOG_FILE}")

    await asyncio.gather(
        ag_bot.connect(),
        muse_bot.connect(),
        ci_bot.connect(),
        builder_bot.connect(),
        card_bot.connect(),
        server.serve_forever()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
