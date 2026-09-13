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

Addressing Rules:
  - "Hi!", "hello", "who is here" -> all bots reply with presence/greetings.
  - "all: ...", "bots: ..."       -> broadcast to all bots.
  - "ag: ...", "muse: ..."        -> direct request to that specific agent only.
  - "ci: ...", "builder: ...", "card: ..." -> direct request to that service bot only.
  - "<other_nick>: ..."           -> addressed to someone else, bots remain silent.

Logs all channel messages and events to irc/logs/channel-refineid.log for agents to read.
"""

import asyncio
import ctypes
import argparse
import datetime
import fcntl
import glob
import json
import os
import re
import shutil
import signal
import socket
import ssl
import sys
import subprocess
import time

ENV_NAME = "prod"
SERVER = "127.0.0.1"
PORT = 6697
USE_TLS = True
TLS_SERVER_HOSTNAME = "oc.daemon.fi"
CHANNELS = ["#refineid"]
SOCKET_PATH = "/tmp/irc-agent-bridge-prod.sock"
DAEMON_LOG_FILE = "/tmp/irc-agent-bridge-prod.log"
LOCK_FILE_OBJ = None

IRC_DIR = os.path.dirname(os.path.abspath(__file__))
CHAT_LOG_DIR = os.path.join(IRC_DIR, "logs")
CHAT_LOG_FILE = os.path.join(CHAT_LOG_DIR, "channel-refineid-prod.log")
SYMLINK_LOG_FILE = "/tmp/irc-channel-refineid.log"

MUSE_BIN = os.environ.get("MUSE_BIN", os.path.expanduser("~/.local/bin/muse"))
AGY_BIN = os.environ.get("AGY_BIN", os.path.expanduser("~/.local/bin/agy"))
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", os.path.expanduser("~/src"))

ALL_BOT_NICKS = ("ag", "antigravity", "agv", "muse", "ci", "gh", "builder", "build", "check", "card", "pcsc")

BOT_ALIASES = {
    "ag": ["ag", "antigravity", "agv"],
    "muse": ["muse"],
    "ci": ["ci", "gh"],
    "builder": ["builder", "build", "check"],
    "card": ["card", "pcsc"],
}

GREETING_EXACT = {
    "hi", "hi!", "hello", "hello!", "hey", "hey!", "hei", "hei!",
    "moro", "moro!", "terve", "terve!", "yo", "yo!", "ping", "ping!"
}
GREETING_PREFIXES = (
    "hi ", "hello ", "hey ", "hei ", "moro ", "terve ", "yo ",
    "who is here", "who is online"
)

AFFIRMATION_EXACT = {
    "cool", "cool!", "nice", "nice!", "awesome", "awesome!",
    "great", "great!", "thanks", "thanks!", "thx", "thx!",
    "good job", "good work", "well done", "kiitos", "kiitos!",
    "sweet", "sweet!", "perfect", "perfect!", "roger", "roger that"
}

GREETING_DELAYS = {
    "ag": 0.0,
    "muse": 0.3,
    "ci": 0.6,
    "builder": 0.9,
    "card": 1.2,
}

LAST_BOT_MESSAGE_TIME = 0.0
LAST_BOT_MESSAGE_SENDER = ""


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


def parse_addressing(text):
    """
    Determines how a message in the channel is addressed.
    Returns (target, mode, query)
    - target: \x27ag\x27, \x27muse\x27, \x27ci\x27, \x27builder\x27, \x27card\x27, \x27all\x27, or other nick string, or None
    - mode: \x27greeting\x27, \x27broadcast\x27, \x27direct\x27, \x27other\x27, \x27unaddressed\x27
    - query: remaining text for the bot to act upon
    """
    stripped = text.strip()
    lower = stripped.lower()

    # 1. Greetings (e.g. "Hi!", "hello everyone")
    if lower in GREETING_EXACT or any(lower.startswith(p) for p in GREETING_PREFIXES):
        return ("all", "greeting", stripped)

    # 2. Explicit broadcasts (e.g. "all:", "bots:", "@all")
    for b in ("all:", "all,", "all ", "bots:", "bots,", "bots ", "@all", "@bots", "everyone:", "everyone,"):
        if lower.startswith(b):
            return ("all", "broadcast", stripped[len(b):].strip())

    # 3. Direct bot addressing (e.g. "ag: ...", "muse: ...", "ci prs")
    for bot, aliases in BOT_ALIASES.items():
        for a in aliases:
            for sep in (":", ",", " "):
                prefix = a + sep
                if lower.startswith(prefix):
                    return (bot, "direct", stripped[len(prefix):].strip())
            at_prefix = "@" + a + " "
            if lower.startswith(at_prefix):
                return (bot, "direct", stripped[len(at_prefix):].strip())
            if lower == a or lower == f"@{a}":
                return (bot, "direct", "")

    # 4. Other user nick addressing (e.g. "petri: ...") -> addressed to someone else!
    m = re.match(r"^([a-zA-Z0-9_\-\[\]]+)[:,]\s*(.*)$", stripped)
    if m:
        return (m.group(1).lower(), "other", m.group(2).strip())

    # 5. Casual affirmations / acknowledgements (e.g. "cool!", "thanks!", "awesome")
    if lower in AFFIRMATION_EXACT:
        if time.time() - LAST_BOT_MESSAGE_TIME < 120:
            target = LAST_BOT_MESSAGE_SENDER if LAST_BOT_MESSAGE_SENDER in BOT_ALIASES else "ag"
            return (target, "affirmation", stripped)

    return (None, "unaddressed", stripped)


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
            proc1 = await asyncio.create_subprocess_exec(
                "cargo", "fmt", "--check", cwd=target_dir,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, err1 = await proc1.communicate()
            if proc1.returncode != 0:
                return f"{rname}: cargo fmt check FAILED:\n{err1.decode()[:180]}"

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

    readers = []
    cards = []
    if sys.platform == "darwin":
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

        try:
            proc = await asyncio.create_subprocess_exec(
                "security", "list-smartcards",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            cards = [l.strip() for l in stdout.decode().splitlines() if l.strip()]
        except Exception:
            pass
    else:
        # Linux (e.g. oc.daemon.fi): Query opensc-tool
        try:
            proc = await asyncio.create_subprocess_exec(
                "opensc-tool", "-l",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            out_lines = [l.strip() for l in stdout.decode().splitlines() if l.strip()]
            for l in out_lines:
                if not l.lower().startswith("no smart card") and not l.lower().startswith("failed"):
                    readers.append(l)
        except Exception as e:
            readers = [f"opensc error: {e}"]

        try:
            proc = await asyncio.create_subprocess_exec(
                "opensc-tool", "-a",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            out_lines = [l.strip() for l in stdout.decode().splitlines() if l.strip()]
            for l in out_lines:
                if not l.lower().startswith("no smart card") and not l.lower().startswith("failed"):
                    cards.append(l)
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
    def __init__(self, bot_id, nick, realname, handler=None, is_logger=False):
        self.bot_id = bot_id
        self.primary_nick = nick
        self.nick = nick
        self.realname = realname
        self.custom_handler = handler
        self.is_logger = is_logger
        self.reader = None
        self.writer = None
        self.running = True
        self.registered = False
        self.ping_task = None
        self.reclaim_task = None
        self.connected_at = 0.0
        self.last_activity = time.time()
        self.reconnect_delay = 3.0

    async def connect(self):
        while self.running:
            self.registered = False
            self.connected_at = 0.0
            try:
                tls_desc = f" (TLS, SNI={TLS_SERVER_HOSTNAME})" if USE_TLS else ""
                log_daemon(f"[{self.nick}] Connecting to {SERVER}:{PORT}{tls_desc} [env={ENV_NAME}]...")
                ssl_ctx = None
                if USE_TLS:
                    ssl_ctx = ssl.create_default_context()
                self.reader, self.writer = await asyncio.open_connection(
                    SERVER, PORT, ssl=ssl_ctx, server_hostname=TLS_SERVER_HOSTNAME if USE_TLS else None
                )

                # Enable TCP Keepalive
                sock = self.writer.get_extra_info('socket')
                if sock:
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                        if hasattr(socket, 'TCP_KEEPIDLE'):
                            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                        elif hasattr(socket, 'TCP_KEEPALIVE'):
                            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, 30)
                        if hasattr(socket, 'TCP_KEEPINTVL'):
                            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                        if hasattr(socket, 'TCP_KEEPCNT'):
                            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                    except Exception:
                        pass

                self.connected_at = time.time()
                self.last_activity = time.time()

                # Send registration
                await self.send_raw(f"NICK {self.nick}")
                await self.send_raw(f"USER {self.nick} 0 * :{self.realname}")

                # Start ping keepalive task
                self.ping_task = asyncio.create_task(self.keepalive_ping_loop())

                await self.listen_loop()
            except Exception as e:
                log_daemon(f"[{self.nick}] Connection error: {e}")
            finally:
                await self.cleanup()

            if not self.running:
                break

            uptime = time.time() - self.connected_at if self.connected_at else 0
            if uptime > 60:
                self.reconnect_delay = 3.0
            else:
                self.reconnect_delay = min(self.reconnect_delay * 1.5, 30.0)

            log_daemon(f"[{self.nick}] Reconnecting in {self.reconnect_delay:.1f}s...")
            await asyncio.sleep(self.reconnect_delay)

    async def send_raw(self, line):
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write((line + "\r\n").encode("utf-8"))
                await asyncio.wait_for(self.writer.drain(), timeout=5.0)
            except Exception as e:
                log_daemon(f"[{self.nick}] Error sending line: {e}")

    def send(self, line):
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write((line + "\r\n").encode("utf-8"))
            except Exception:
                pass

    async def keepalive_ping_loop(self):
        try:
            while self.running and self.writer and not self.writer.is_closing():
                await asyncio.sleep(45)
                if time.time() - self.last_activity >= 40:
                    await self.send_raw(f"PING :{SERVER}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log_daemon(f"[{self.nick}] Keepalive ping loop error: {e}")

    async def cleanup(self):
        if self.ping_task and not self.ping_task.done():
            self.ping_task.cancel()
        if self.reclaim_task and not self.reclaim_task.done():
            self.reclaim_task.cancel()
        if self.writer:
            try:
                if not self.writer.is_closing():
                    self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
        self.reader = None

    async def shutdown(self, reason="RefineID daemon restart"):
        self.running = False
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write(f"QUIT :{reason}\r\n".encode("utf-8"))
                await asyncio.wait_for(self.writer.drain(), timeout=2.0)
            except Exception:
                pass
        await self.cleanup()

    async def privmsg(self, target, msg):
        global LAST_BOT_MESSAGE_TIME, LAST_BOT_MESSAGE_SENDER
        LAST_BOT_MESSAGE_TIME = time.time()
        LAST_BOT_MESSAGE_SENDER = self.bot_id

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
                await self.send_raw(f"PRIVMSG {target} :{chunk}")
                await asyncio.sleep(0.1)
            await self.send_raw(f"PRIVMSG {target} :{line}")
            await asyncio.sleep(0.08)

    async def listen_loop(self):
        while self.running and self.reader:
            try:
                line = await self.reader.readline()
            except Exception as e:
                log_daemon(f"[{self.nick}] Socket read error: {e}")
                break
            if not line:
                log_daemon(f"[{self.nick}] Connection closed by remote")
                break

            self.last_activity = time.time()
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue

            # Handle PING
            if raw.startswith("PING "):
                token = raw[5:]
                await self.send_raw(f"PONG {token}")
                continue

            parts = raw.split(" ")
            if len(parts) >= 2 and parts[1] == "PING":
                token = parts[2] if len(parts) > 2 else ""
                await self.send_raw(f"PONG {token}")
                continue

            if len(parts) < 2:
                continue

            prefix = parts[0]
            cmd = parts[1]

            # 001 RPL_WELCOME: successfully registered with IRCd
            if cmd == "001":
                self.registered = True
                log_daemon(f"[{self.nick}] Registered with IRCd. Joining {CHANNELS}...")
                for ch in CHANNELS:
                    await self.send_raw(f"JOIN {ch}")
                continue

            # 433 ERR_NICKNAMEINUSE: Nickname is already in use (ghost connection)
            elif cmd == "433":
                log_daemon(f"[{self.nick}] Nick '{self.nick}' in use. Using fallback '{self.nick}_'...")
                self.nick = f"{self.nick}_"
                await self.send_raw(f"NICK {self.nick}")
                await self.send_raw(f"USER {self.nick} 0 * :{self.realname}")
                for ch in CHANNELS:
                    await self.send_raw(f"JOIN {ch}")
                if not self.reclaim_task or self.reclaim_task.done():
                    self.reclaim_task = asyncio.create_task(self.reclaim_nick_loop())
                continue

            # Nick change acknowledgement
            elif cmd == "NICK" and len(parts) >= 3:
                sender = prefix[1:].split("!", 1)[0] if prefix.startswith(":") else ""
                new_nick = parts[2][1:] if parts[2].startswith(":") else parts[2]
                if sender == self.nick:
                    self.nick = new_nick
                    log_daemon(f"[{self.bot_id}] Nick confirmed changed to: {self.nick}")

            elif cmd == "PRIVMSG" and len(parts) >= 4:
                sender = prefix[1:].split("!", 1)[0] if prefix.startswith(":") else ""
                channel = parts[2]
                trailing = " ".join(parts[3:])
                text = trailing[1:] if trailing.startswith(":") else trailing

                # Only designated logger logs incoming channel chat
                if self.is_logger and channel.startswith("#"):
                    if sender not in ALL_BOT_NICKS:
                        log_chat(f"<{sender}> {text}")

                if sender in ALL_BOT_NICKS:
                    continue

                try:
                    await self.handle_channel_message(sender, channel, text)
                except Exception as e:
                    log_daemon(f"[{self.nick}] Error handling message from {sender}: {e}")

            elif self.is_logger:
                sender = prefix[1:].split("!", 1)[0] if prefix.startswith(":") else ""
                if cmd == "JOIN" and len(parts) >= 3:
                    chan = parts[2][1:] if parts[2].startswith(":") else parts[2]
                    log_chat(f"* {sender} joined {chan}")
                elif cmd == "PART" and len(parts) >= 3:
                    chan = parts[2]
                    reason = " ".join(parts[3:])[1:] if len(parts) > 3 and parts[3].startswith(":") else ""
                    log_chat(f"* {sender} left {chan}" + (f" ({reason})" if reason else ""))
                elif cmd == "QUIT":
                    reason = " ".join(parts[2:])[1:] if len(parts) > 2 and parts[2].startswith(":") else ""
                    log_chat(f"* {sender} quit" + (f" ({reason})" if reason else ""))
                elif cmd == "TOPIC" and len(parts) >= 4:
                    chan = parts[2]
                    topic = " ".join(parts[3:])[1:] if parts[3].startswith(":") else " ".join(parts[3:])
                    log_chat(f"* {sender} changed topic of {chan} to: {topic}")

    async def reclaim_nick_loop(self):
        try:
            while self.running and self.nick != self.primary_nick:
                await asyncio.sleep(10)
                log_daemon(f"[{self.nick}] Attempting to reclaim primary nick '{self.primary_nick}'...")
                await self.send_raw(f"NICK {self.primary_nick}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log_daemon(f"[{self.nick}] Error in reclaim nick loop: {e}")

    async def handle_channel_message(self, sender, channel, text):
        if sender.lower() in ALL_BOT_NICKS:
            return

        target = channel if channel.startswith("#") else sender
        addressed_to, mode, query = parse_addressing(text)

        # If addressed to someone else (another nick or user), stay silent
        if mode == "other":
            return
        if mode == "unaddressed" and addressed_to is None:
            return

        # Case 0: Affirmations and casual acknowledgements (e.g. "cool!", "thanks!")
        if mode == "affirmation":
            if addressed_to == self.bot_id:
                if self.bot_id == "ag":
                    await self.privmsg(target, f"Thanks {sender}! Ready for reviews, builds, and coding tasks (ask 'ag: <task>').")
                elif self.bot_id == "muse":
                    await self.privmsg(target, f"Glad to help {sender}! Let me know if you need code review or refactoring.")
                elif self.bot_id == "ci":
                    await self.privmsg(target, f"Anytime {sender}! Tracking PRs and CI checks.")
                elif self.bot_id == "builder":
                    await self.privmsg(target, f"Ready {sender}! Let me know when you want to run checks or tests.")
                elif self.bot_id == "card":
                    await self.privmsg(target, f"Monitoring readers and card tokens 24/7, {sender}!")
            return

        # Case 1: All bots respond (Greetings or Broadcasts)
        if addressed_to == "all":
            delay = GREETING_DELAYS.get(self.bot_id, 0.0)
            await asyncio.sleep(delay)

            if mode == "greeting":
                if self.bot_id == "ag":
                    await self.privmsg(target, f"Hi {sender}! Antigravity coding agent online (ask 'ag: <task>').")
                elif self.bot_id == "muse":
                    await self.privmsg(target, f"Hi {sender}! Muse code agent online (ask 'muse: <task>').")
                elif self.bot_id == "ci":
                    await self.privmsg(target, f"Hi {sender}! CI & GitHub bot online (ask 'ci help').")
                elif self.bot_id == "builder":
                    await self.privmsg(target, f"Hi {sender}! Build & verifier bot online (ask 'builder help').")
                elif self.bot_id == "card":
                    await self.privmsg(target, f"Hi {sender}! Smart card monitor online (ask 'card help').")
                return

            elif mode == "broadcast":
                if query.lower() in ("help", "commands"):
                    if self.bot_id == "ag":
                        await self.privmsg(target, "ag: AI coding agent for reviews, code changes, and PR debates.")
                    elif self.bot_id == "muse":
                        await self.privmsg(target, "muse: Muse code agent for reviews and code changes.")
                    elif self.bot_id == "ci":
                        await self.privmsg(target, "ci: GitHub PRs and CI run tracking (type 'ci help').")
                    elif self.bot_id == "builder":
                        await self.privmsg(target, "builder: Local test suites, formatting, and git status (type 'builder help').")
                    elif self.bot_id == "card":
                        await self.privmsg(target, "card: Smart card readers & token monitor (type 'card help').")
                    return
                elif query.lower() in ("status", "st"):
                    if self.bot_id == "ag":
                        await self.privmsg(target, "ag: online, idle.")
                    elif self.bot_id == "muse":
                        await self.privmsg(target, "muse: online, idle.")
                    elif self.custom_handler:
                        reply = await self.custom_handler(sender, "status")
                        await self.privmsg(target, f"{reply}")
                    return

        # Case 2: Addressed directly to THIS bot
        if addressed_to == self.bot_id:
            # Handle casual positive replies addressed directly (e.g. "ag: cool", "ag: thanks")
            if query.lower() in AFFIRMATION_EXACT:
                if self.bot_id == "ag":
                    await self.privmsg(target, f"Thanks {sender}! Ready for reviews, builds, and coding tasks (ask 'ag: <task>').")
                else:
                    await self.privmsg(target, f"Glad to help {sender}!")
                return

            log_daemon(f"[{self.nick}] Triggered by {sender} in {target}: {query}")
            if self.custom_handler:
                reply = await self.custom_handler(sender, query)
                await self.privmsg(target, f"{sender}: {reply}")
            else:
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

            reply = ""
            gemini_api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

            # 1. Try Google Gemini API if key is configured
            if gemini_api_key and self.nick in ("ag", "antigravity", "agv"):
                try:
                    from google import genai
                    client = genai.Client(api_key=gemini_api_key)
                    sys_inst = (
                        "You are Antigravity ('ag'), the official Google AI coding assistant on RefineID IRC (#refineid).\n"
                        "You run as a permanent daemon on oc.daemon.fi.\n"
                        "Follow AGENTS.md rules strictly:\n"
                        "- Zero PIN and PIN-length logging across all environments.\n"
                        "- Preserve ISO-8859-15 encoding and specification symbols (§).\n"
                        "- Safe Rust owns protocol, parsing, and secrets; unsafe is confined to PC/SC boundaries.\n"
                        "- Zero AI attribution in commits.\n"
                        "Provide concise, precise, technical responses formatted for IRC lines (under 400 chars per line)."
                    )
                    log_daemon(f"[{self.nick}] Calling Gemini API for {sender}...")
                    resp = await asyncio.to_thread(
                        client.models.generate_content,
                        model="gemini-2.5-flash",
                        contents=full_prompt,
                        config=dict(system_instruction=sys_inst)
                    )
                    if resp and resp.text:
                        reply = resp.text.strip()
                except Exception as e:
                    log_daemon(f"[{self.nick}] Gemini API call failed: {e}")

            # 2. Try CLI execution (agy or muse) if available
            if not reply:
                cmd = None
                if self.nick == "muse" and shutil.which(MUSE_BIN):
                    cmd = [MUSE_BIN, "exec", "--yolo", full_prompt]
                elif shutil.which(AGY_BIN):
                    cmd = [AGY_BIN, "--dangerously-skip-permissions", "--print", full_prompt]

                if cmd:
                    log_daemon(f"[{self.nick}] Executing CLI: {' '.join(cmd)}")
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

            # 3. Built-in autonomous daemon intelligence on oc.daemon.fi
            if not reply:
                reply = await handle_autonomous_query(self.nick, sender, query)

            log_daemon(f"[{self.nick}] Finished query for {sender}, reply length: {len(reply)} chars")
            await self.privmsg(target, f"{sender}: {reply}")
        except Exception as e:
            log_daemon(f"[{self.nick}] Error executing agent query: {e}")
            await self.privmsg(target, f"{sender}: Error executing agent: {e}")


async def handle_autonomous_query(nick, sender, query):
    """Autonomous built-in intelligence when running as a permanent cloud daemon."""
    import platform
    q = query.strip()
    q_lower = q.lower()

    # 1. System, Host, and Daemon Info
    if any(k in q_lower for k in ("uptime", "host", "where are you", "who are you", "daemon", "server", "status")):
        uname = platform.uname()
        mem_info = "unknown"
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemAvailable" in line:
                        mem_info = line.split(":")[1].strip()
                        break
        except Exception:
            pass
        return (
            f"Antigravity ('{nick}') running as permanent daemon on {uname.node}.\n"
            f"  Host   : {uname.system} {uname.release} ({uname.machine})\n"
            f"  Channel: #refineid (ngIRCd :6667 / :6697 TLS)\n"
            f"  Memory : {mem_info} available\n"
            f"  Status : Active 24/7 permanent review and coordination daemon."
        )

    # 2. AGENTS.md Governing Rules
    if any(k in q_lower for k in ("rule", "rules", "spec", "pin", "agents.md")):
        return (
            "AGENTS.md Governing Rules:\n"
            "  1. ISO-8859-15 encoding: Preserve meaningful symbols (§, €); never degrade to ASCII.\n"
            "  2. Zero PIN logging: Never log, trace, or format PIN bytes or lengths across all environments.\n"
            "  3. Safe Rust boundaries: Safe Rust owns protocol & parsing; unsafe inside PC/SC boundary.\n"
            "  4. Windows ABI validation: Pointer nullability & length must be validated before deref.\n"
            "  5. Zero AI attribution in git commits."
        )

    # 3. Git / Codebase queries
    if any(k in q_lower for k in ("git", "repo", "commit", "diff", "branch", "pr", "log")):
        repos = []
        if os.path.isdir(WORKSPACE_DIR):
            for d in sorted(os.listdir(WORKSPACE_DIR)):
                full = os.path.join(WORKSPACE_DIR, d)
                if os.path.isdir(os.path.join(full, ".git")):
                    repos.append(d)
        if ("log" in q_lower or "commit" in q_lower) and repos:
            rpath = os.path.join(WORKSPACE_DIR, repos[0])
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", rpath, "log", "-n", "3", "--oneline",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                return f"Latest commits in {repos[0]}:\n" + stdout.decode().strip()
            except Exception:
                pass
        if repos:
            return f"Cloned repositories on oc.daemon.fi ({len(repos)}): " + ", ".join(repos)
        return "No local repositories cloned yet in " + WORKSPACE_DIR

    # 4. Default helpful response with Gemini API setup instructions
    return (
        f"I received your request: '{q}'.\n"
        f"I am running as a permanent daemon on oc.daemon.fi. "
        f"To enable direct generative Gemini LLM turns, set GEMINI_API_KEY in /home/pk/.config/refineid/env. "
        f"Available built-in commands: 'ci: prs', 'builder: status', 'card: status', 'ag: rules', 'ag: status'."
    )


async def handle_unix_client(reader, writer, bots):
    data = await reader.read(65536)
    writer.close()
    await writer.wait_closed()

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


def configure_environment():
    global ENV_NAME, SERVER, PORT, USE_TLS, TLS_SERVER_HOSTNAME, CHANNELS
    global SOCKET_PATH, DAEMON_LOG_FILE, CHAT_LOG_FILE

    parser = argparse.ArgumentParser(description="RefineID Multi-Agent IRC Review Bridge")
    parser.add_argument(
        "--env",
        choices=["prod", "test", "local", "oc"],
        default=os.environ.get("IRC_ENV", "prod"),
        help="Target environment: 'prod' (oc.daemon.fi, default) or 'test' (local)"
    )
    parser.add_argument("--test", action="store_const", const="test", dest="env", help="Shortcut for --env test")
    parser.add_argument("--prod", action="store_const", const="prod", dest="env", help="Shortcut for --env prod")
    parser.add_argument("--server", default=None, help="IRC server address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="IRC server port (default: 6697 for prod, 6667 for test)")
    parser.add_argument("--tls", dest="use_tls", action="store_true", default=None, help="Force TLS encryption")
    parser.add_argument("--no-tls", dest="use_tls", action="store_false", help="Disable TLS encryption")
    parser.add_argument("--tls-host", default=os.environ.get("IRC_TLS_HOST", "oc.daemon.fi"), help="TLS SNI hostname")
    parser.add_argument("--channel", action="append", dest="channels", help="Channel to join (default: #refineid)")

    args, _ = parser.parse_known_args()

    target_env = "test" if args.env in ("test", "local") else "prod"
    ENV_NAME = target_env

    if ENV_NAME == "prod":
        SERVER = args.server or os.environ.get("IRC_SERVER", "127.0.0.1")
        PORT = args.port or int(os.environ.get("IRC_PORT", 6697))
        USE_TLS = True if args.use_tls is None else args.use_tls
        TLS_SERVER_HOSTNAME = args.tls_host
        SOCKET_PATH = "/tmp/irc-agent-bridge-prod.sock"
        DAEMON_LOG_FILE = "/tmp/irc-agent-bridge-prod.log"
        CHAT_LOG_FILE = os.path.join(CHAT_LOG_DIR, "channel-refineid-prod.log")
    else:
        SERVER = args.server or os.environ.get("IRC_SERVER", "127.0.0.1")
        PORT = args.port or int(os.environ.get("IRC_PORT", 6667))
        USE_TLS = False if args.use_tls is None else args.use_tls
        TLS_SERVER_HOSTNAME = args.tls_host
        SOCKET_PATH = "/tmp/irc-agent-bridge-test.sock"
        DAEMON_LOG_FILE = "/tmp/irc-agent-bridge-test.log"
        CHAT_LOG_FILE = os.path.join(CHAT_LOG_DIR, "channel-refineid-test.log")

    if args.channels:
        CHANNELS = args.channels


def acquire_process_lock():
    global LOCK_FILE_OBJ
    lock_path = f"/tmp/irc-agent-bridge-{ENV_NAME}.pid"
    try:
        f = open(lock_path, "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(f"{os.getpid()}\n")
        f.flush()
        LOCK_FILE_OBJ = f
        return True
    except (BlockingIOError, IOError):
        log_daemon(f"Another instance of irc-agent-bridge ({ENV_NAME}) is already active. Exiting cleanly.")
        return False


def release_process_lock():
    global LOCK_FILE_OBJ
    if LOCK_FILE_OBJ:
        try:
            fcntl.flock(LOCK_FILE_OBJ, fcntl.LOCK_UN)
            LOCK_FILE_OBJ.close()
        except Exception:
            pass
        LOCK_FILE_OBJ = None
        lock_path = f"/tmp/irc-agent-bridge-{ENV_NAME}.pid"
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except OSError:
                pass


async def main():
    configure_environment()
    ensure_log_dir()

    if not acquire_process_lock():
        sys.exit(0)

    if os.path.exists(SOCKET_PATH):
        try:
            os.remove(SOCKET_PATH)
        except OSError:
            pass

    # Maintain canonical socket and log symlinks
    canonical_sock = "/tmp/irc-agent-bridge.sock"
    try:
        if os.path.islink(canonical_sock) or os.path.exists(canonical_sock):
            os.remove(canonical_sock)
        os.symlink(SOCKET_PATH, canonical_sock)
    except OSError:
        pass

    canonical_log = "/tmp/irc-channel-refineid.log"
    try:
        if os.path.islink(canonical_log) or os.path.exists(canonical_log):
            os.remove(canonical_log)
        os.symlink(CHAT_LOG_FILE, canonical_log)
    except OSError:
        pass

    # 1. AI Coding Agents
    ag_bot = IrcBot(
        bot_id="ag", nick="ag", realname="Google Antigravity Agent",
        is_logger=True
    )
    muse_bot = IrcBot(
        bot_id="muse", nick="muse", realname="Muse Code Agent"
    )

    # 2. Service Bots
    ci_bot = IrcBot(
        bot_id="ci", nick="ci", realname="RefineID CI & GitHub Bot",
        handler=handle_ci_query
    )
    builder_bot = IrcBot(
        bot_id="builder", nick="builder", realname="RefineID Build & Verifier Bot",
        handler=handle_builder_query
    )
    card_bot = IrcBot(
        bot_id="card", nick="card", realname="RefineID Smart Card Monitor",
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

    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def on_stop_signal():
        log_daemon("Termination signal received. Shutting down gracefully...")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, on_stop_signal)
        except NotImplementedError:
            pass

    tasks = [
        asyncio.create_task(ag_bot.connect()),
        asyncio.create_task(muse_bot.connect()),
        asyncio.create_task(ci_bot.connect()),
        asyncio.create_task(builder_bot.connect()),
        asyncio.create_task(card_bot.connect()),
        asyncio.create_task(server.serve_forever()),
    ]

    try:
        wait_task = asyncio.create_task(stop_event.wait())
        await asyncio.wait([wait_task] + tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        log_daemon("Disconnecting IRC bots cleanly...")
        all_bots = [ag_bot, muse_bot, ci_bot, builder_bot, card_bot]
        await asyncio.gather(*(b.shutdown("Service restart") for b in all_bots), return_exceptions=True)
        server.close()
        await server.wait_closed()
        release_process_lock()
        log_daemon("IRC Agent Bridge shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        release_process_lock()
