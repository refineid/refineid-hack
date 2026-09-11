#!/usr/bin/env python3
"""
m1-agent.py - Dedicated persistent IRC agent daemon for m1.local.
Connects to #refineid on oc.daemon.fi as nickname 'm1' (Antigravity on m1.local).
Acts based on IRC discussion, executes local cargo builds, tests, clippy,
git operations, and Antigravity/Muse tasks directly in /Users/pk/src/refineid-windows.

Governed by AGENTS.md rules:
  - Zero PIN and PIN-length logging across all environments.
  - Preserve ISO-8859-15 encoding and specification symbols (§).
  - Safe Rust owns protocol & parsing; unsafe confined to PC/SC boundaries.
  - Zero AI attribution in commits.
"""

import asyncio
import argparse
import datetime
import fcntl
import glob
import json
import os
import platform
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time

SERVER = "127.0.0.1"
PORT = 6697
USE_TLS = True
TLS_SERVER_HOSTNAME = "oc.daemon.fi"
CHANNELS = ["#refineid"]
NICK = "m1"
REALNAME = "Antigravity Agent on m1.local (macOS)"

SOCKET_PATH = "/tmp/m1-irc-agent.sock"
LOCK_PATH = "/tmp/m1-irc-agent.pid"
DAEMON_LOG = "/tmp/m1-irc-agent.log"
PENDING_TASKS_FILE = "/tmp/m1-pending-tasks.jsonl"
SYMLINK_LOG = "/tmp/m1-channel-refineid.log"

IRC_DIR = os.path.dirname(os.path.abspath(__file__))
CHAT_LOG_DIR = os.path.join(IRC_DIR, "logs")
CHAT_LOG_FILE = os.path.join(CHAT_LOG_DIR, "channel-refineid-m1.log")

WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", os.path.expanduser("~/src/refineid-windows"))
MUSE_BIN = os.environ.get("MUSE_BIN", os.path.expanduser("~/.local/bin/muse"))
AGY_BIN = os.environ.get("AGY_BIN", os.path.expanduser("~/.local/bin/agy"))

LOCK_FILE_OBJ = None
LAST_M1_MESSAGE_TIME = 0.0

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


def log_daemon(msg):
    line = f"[m1-agent] {msg}\n"
    sys.stderr.write(line)
    sys.stderr.flush()
    try:
        with open(DAEMON_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def log_chat(entry):
    os.makedirs(CHAT_LOG_DIR, exist_ok=True)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{now_str}] {entry}\n"
    try:
        with open(CHAT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted)
            f.flush()
    except Exception as e:
        log_daemon(f"Error writing chat log: {e}")

    try:
        if not os.path.islink(SYMLINK_LOG) and not os.path.exists(SYMLINK_LOG):
            os.symlink(CHAT_LOG_FILE, SYMLINK_LOG)
    except Exception:
        pass


def record_task_queue(sender, task_text):
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "sender": sender,
        "task": task_text,
        "status": "received"
    }
    try:
        with open(PENDING_TASKS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log_daemon(f"Error logging task queue: {e}")


def parse_addressing(text):
    stripped = text.strip()
    lower = stripped.lower()

    # 1. Greetings (e.g. "Hi!", "hello everyone")
    if lower in GREETING_EXACT or any(lower.startswith(p) for p in GREETING_PREFIXES):
        return ("all", "greeting", stripped)

    # 2. Broadcasts
    for b in ("all:", "all,", "all ", "bots:", "bots,", "bots ", "@all", "@bots", "everyone:", "everyone,"):
        if lower.startswith(b):
            return ("all", "broadcast", stripped[len(b):].strip())

    # 3. Direct addressing to m1 (e.g. "m1: ...", "m1, ...", "@m1 ...", "m1 ...")
    for sep in (":", ",", " "):
        prefix = "m1" + sep
        if lower.startswith(prefix):
            return ("m1", "direct", stripped[len(prefix):].strip())
    if lower.startswith("@m1 "):
        return ("m1", "direct", stripped[4:].strip())
    if lower == "m1" or lower == "@m1":
        return ("m1", "direct", "")

    # 4. Other user nick addressing (e.g. "ag: ...", "petri: ...")
    m = re.match(r"^([a-zA-Z0-9_\-\[\]]+)[:,]\s*(.*)$", stripped)
    if m:
        return (m.group(1).lower(), "other", m.group(2).strip())

    # 5. Casual affirmations
    if lower in AFFIRMATION_EXACT:
        if time.time() - LAST_M1_MESSAGE_TIME < 120:
            return ("m1", "affirmation", stripped)

    return (None, "unaddressed", stripped)


# ----------------------------------------------------------------------
# Local Task Execution Handlers on m1.local
# ----------------------------------------------------------------------

async def handle_status():
    uname = platform.uname()
    branch = "unknown"
    uncommitted = 0
    if os.path.isdir(os.path.join(WORKSPACE_DIR, ".git")):
        try:
            b_proc = await asyncio.create_subprocess_exec(
                "git", "-C", WORKSPACE_DIR, "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            b_out, _ = await b_proc.communicate()
            branch = b_out.decode().strip() or "detached"

            s_proc = await asyncio.create_subprocess_exec(
                "git", "-C", WORKSPACE_DIR, "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            s_out, _ = await s_proc.communicate()
            uncommitted = len([l for l in s_out.decode().splitlines() if l.strip()])
        except Exception:
            pass

    # Check smart card readers on macOS
    readers_desc = "None detected"
    try:
        proc = await asyncio.create_subprocess_exec(
            "security", "list-smartcards",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        r_lines = [l.strip() for l in stdout.decode().splitlines() if l.strip() and "No smartcards" not in l]
        if r_lines:
            readers_desc = f"{len(r_lines)} reader(s) active"
    except Exception:
        pass

    clean_str = f"{uncommitted} uncommitted file(s)" if uncommitted else "clean"
    return (
        f"Antigravity ('m1') active on {uname.node} (Apple Silicon {uname.machine}).\n"
        f"  Host     : {uname.system} {uname.release}\n"
        f"  Workspace: {WORKSPACE_DIR} [{branch}, {clean_str}]\n"
        f"  Readers  : {readers_desc}\n"
        f"  AGENTS.md: Active (Zero PIN logging, Safe Rust boundary, ISO-8859-15 §)."
    )


async def handle_cargo_check():
    if not os.path.isfile(os.path.join(WORKSPACE_DIR, "Cargo.toml")):
        return f"Cargo.toml not found in {WORKSPACE_DIR}"
    proc = await asyncio.create_subprocess_exec(
        "cargo", "check", "--workspace", "--all-targets",
        cwd=WORKSPACE_DIR,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    out = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode == 0:
        return "cargo check: PASSED clean across all workspace targets."
    else:
        err_lines = [l for l in out.splitlines() if "error" in l.lower()][:4]
        return f"cargo check: FAILED (exit {proc.returncode}):\n" + "\n".join(err_lines)


async def handle_cargo_test():
    if not os.path.isfile(os.path.join(WORKSPACE_DIR, "Cargo.toml")):
        return f"Cargo.toml not found in {WORKSPACE_DIR}"
    proc = await asyncio.create_subprocess_exec(
        "cargo", "test", "--workspace", "--lib", "--quiet",
        cwd=WORKSPACE_DIR,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    summary = [l for l in (out + "\n" + err).splitlines() if "test result:" in l]
    if proc.returncode == 0:
        return "cargo test: PASSED.\n" + ("\n".join(summary) if summary else "All tests passed.")
    else:
        return f"cargo test: FAILED (exit {proc.returncode}):\n" + ("\n".join(summary) if summary else err[:200])


async def handle_cargo_clippy():
    if not os.path.isfile(os.path.join(WORKSPACE_DIR, "Cargo.toml")):
        return f"Cargo.toml not found in {WORKSPACE_DIR}"
    proc = await asyncio.create_subprocess_exec(
        "cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings",
        cwd=WORKSPACE_DIR,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    out = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode == 0:
        return "cargo clippy -D warnings: PASSED clean."
    else:
        warns = [l for l in out.splitlines() if "error:" in l or "warning:" in l][:4]
        return f"cargo clippy: FAILED:\n" + "\n".join(warns)


async def handle_git_diff():
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", WORKSPACE_DIR, "diff", "--stat",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace").strip()
    if not out:
        return "git diff: Working tree clean (no unstaged changes)."
    return f"git diff in {os.path.basename(WORKSPACE_DIR)}:\n{out}"


async def handle_git_log():
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", WORKSPACE_DIR, "log", "-n", "3", "--oneline",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace").strip()
    return f"Latest commits in {os.path.basename(WORKSPACE_DIR)}:\n{out}"


async def handle_agent_dispatch(sender, query):
    """Dispatches complex tasks to local agy CLI or muse CLI."""
    record_task_queue(sender, query)
    cmd = None
    if "muse" in query.lower() and shutil.which(MUSE_BIN):
        cmd = [MUSE_BIN, "exec", "--yolo", query]
    elif shutil.which(AGY_BIN):
        cmd = [AGY_BIN, "--dangerously-skip-permissions", "--print", query]

    if not cmd:
        return f"Received task: '{query}'. (agy CLI not located; queued for Antigravity)."

    log_daemon(f"Executing local agent CLI: {' '.join(cmd[:3])}...")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKSPACE_DIR
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        if out_text:
            return out_text
        elif err_text:
            return f"stderr: {err_text[:200]}"
        return "(done, clean output)"
    except asyncio.TimeoutError:
        return f"Task '{query[:30]}' timed out after 120s on m1.local."
    except Exception as e:
        return f"Error executing task on m1.local: {e}"


# ----------------------------------------------------------------------
# IRC Client Engine for m1
# ----------------------------------------------------------------------

class M1IrcClient:
    def __init__(self):
        self.primary_nick = NICK
        self.nick = NICK
        self.realname = REALNAME
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
                log_daemon(f"Connecting to {SERVER}:{PORT}{tls_desc}...")
                ssl_ctx = ssl.create_default_context() if USE_TLS else None
                self.reader, self.writer = await asyncio.open_connection(
                    SERVER, PORT, ssl=ssl_ctx, server_hostname=TLS_SERVER_HOSTNAME if USE_TLS else None
                )

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

                await self.send_raw(f"NICK {self.nick}")
                await self.send_raw(f"USER {self.nick} 0 * :{self.realname}")

                self.ping_task = asyncio.create_task(self.keepalive_ping_loop())
                await self.listen_loop()
            except Exception as e:
                log_daemon(f"Connection error: {e}")
            finally:
                await self.cleanup()

            if not self.running:
                break

            uptime = time.time() - self.connected_at if self.connected_at else 0
            if uptime > 60:
                self.reconnect_delay = 3.0
            else:
                self.reconnect_delay = min(self.reconnect_delay * 1.5, 30.0)

            log_daemon(f"Reconnecting in {self.reconnect_delay:.1f}s...")
            await asyncio.sleep(self.reconnect_delay)

    async def send_raw(self, line):
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write((line + "\r\n").encode("utf-8"))
                await asyncio.wait_for(self.writer.drain(), timeout=5.0)
            except Exception as e:
                log_daemon(f"Error sending line: {e}")

    async def keepalive_ping_loop(self):
        try:
            while self.running and self.writer and not self.writer.is_closing():
                await asyncio.sleep(45)
                if time.time() - self.last_activity >= 40:
                    await self.send_raw(f"PING :{SERVER}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log_daemon(f"Keepalive ping loop error: {e}")

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

    async def shutdown(self, reason="m1 daemon reload"):
        self.running = False
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write(f"QUIT :{reason}\r\n".encode("utf-8"))
                await asyncio.wait_for(self.writer.drain(), timeout=2.0)
            except Exception:
                pass
        await self.cleanup()

    async def privmsg(self, target, msg):
        global LAST_M1_MESSAGE_TIME
        LAST_M1_MESSAGE_TIME = time.time()

        for line in msg.strip().splitlines():
            line = line.strip()
            if not line:
                continue

            log_chat(f"<{self.nick}> {line}")

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
                log_daemon(f"Socket read error: {e}")
                break
            if not line:
                log_daemon("Connection closed by remote")
                break

            self.last_activity = time.time()
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue

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

            if cmd == "001":
                self.registered = True
                log_daemon(f"Registered with IRCd. Joining {CHANNELS}...")
                for ch in CHANNELS:
                    await self.send_raw(f"JOIN {ch}")
                continue

            elif cmd == "433":
                log_daemon(f"Nick '{self.nick}' in use. Using fallback '{self.nick}_'...")
                self.nick = f"{self.nick}_"
                await self.send_raw(f"NICK {self.nick}")
                await self.send_raw(f"USER {self.nick} 0 * :{self.realname}")
                for ch in CHANNELS:
                    await self.send_raw(f"JOIN {ch}")
                if not self.reclaim_task or self.reclaim_task.done():
                    self.reclaim_task = asyncio.create_task(self.reclaim_nick_loop())
                continue

            elif cmd == "NICK" and len(parts) >= 3:
                sender = prefix[1:].split("!", 1)[0] if prefix.startswith(":") else ""
                new_nick = parts[2][1:] if parts[2].startswith(":") else parts[2]
                if sender == self.nick:
                    self.nick = new_nick
                    log_daemon(f"Nick confirmed changed to: {self.nick}")

            elif cmd == "PRIVMSG" and len(parts) >= 4:
                sender = prefix[1:].split("!", 1)[0] if prefix.startswith(":") else ""
                channel = parts[2]
                trailing = " ".join(parts[3:])
                text = trailing[1:] if trailing.startswith(":") else trailing

                if channel.startswith("#"):
                    log_chat(f"<{sender}> {text}")

                if sender == self.nick or sender.startswith("m1"):
                    continue

                try:
                    await self.handle_message(sender, channel, text)
                except Exception as e:
                    log_daemon(f"Error handling message from {sender}: {e}")

    async def reclaim_nick_loop(self):
        try:
            while self.running and self.nick != self.primary_nick:
                await asyncio.sleep(10)
                log_daemon(f"Attempting to reclaim primary nick '{self.primary_nick}'...")
                await self.send_raw(f"NICK {self.primary_nick}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log_daemon(f"Error in reclaim loop: {e}")

    async def handle_message(self, sender, channel, text):
        target = channel if channel.startswith("#") else sender
        addressed_to, mode, query = parse_addressing(text)

        if mode == "other":
            return
        if mode == "unaddressed" and addressed_to is None:
            return

        # 1. Affirmations
        if mode == "affirmation" and (addressed_to == "m1" or addressed_to is None):
            await self.privmsg(target, f"Thanks {sender}! Standing by on m1.local for builds, tests, or coding tasks.")
            return

        # 2. Greetings
        if addressed_to == "all":
            if mode == "greeting":
                await asyncio.sleep(1.5)  # Stagger after cloud bots
                await self.privmsg(target, f"Hi {sender}! Antigravity local agent on m1.local online (ask 'm1: <task>').")
                return
            elif mode == "broadcast":
                if query.lower() in ("help", "commands"):
                    await self.privmsg(target, "m1: Local M1 Mac agent - cargo builds, clippy, tests, and local coding tasks (ask 'm1: <task>').")
                    return
                elif query.lower() in ("status", "st"):
                    reply = await handle_status()
                    await self.privmsg(target, reply)
                    return

        # 3. Direct addressing to m1
        if addressed_to == "m1":
            q_lower = query.lower().strip()
            log_daemon(f"Triggered by {sender} in {target}: {query}")

            if q_lower in AFFIRMATION_EXACT:
                await self.privmsg(target, f"Thanks {sender}! Ready on m1.local.")
                return

            if q_lower in ("status", "st", "host", "info"):
                reply = await handle_status()
                await self.privmsg(target, f"{sender}: {reply}")
                return

            if q_lower in ("help", "--help", "-h"):
                help_text = (
                    f"Commands for m1 (Antigravity on m1.local):\n"
                    f"  m1: status         - Host info, workspace git status, hardware\n"
                    f"  m1: check          - Run cargo check in refineid-windows\n"
                    f"  m1: test           - Run cargo test unit test suite\n"
                    f"  m1: clippy         - Run cargo clippy -D warnings\n"
                    f"  m1: diff           - Show git diff stat of local working tree\n"
                    f"  m1: log            - Show latest commits on m1\n"
                    f"  m1: <prompt>       - Dispatch uninhibited task to local agy/muse"
                )
                await self.privmsg(target, f"{sender}: {help_text}")
                return

            if q_lower in ("check", "cargo check"):
                await self.privmsg(target, f"{sender}: Running cargo check on m1.local...")
                reply = await handle_cargo_check()
                await self.privmsg(target, f"{sender}: {reply}")
                return

            if q_lower in ("test", "cargo test"):
                await self.privmsg(target, f"{sender}: Running cargo test on m1.local...")
                reply = await handle_cargo_test()
                await self.privmsg(target, f"{sender}: {reply}")
                return

            if q_lower in ("clippy", "cargo clippy"):
                await self.privmsg(target, f"{sender}: Running cargo clippy on m1.local...")
                reply = await handle_cargo_clippy()
                await self.privmsg(target, f"{sender}: {reply}")
                return

            if q_lower in ("diff", "git diff", "diffstat"):
                reply = await handle_git_diff()
                await self.privmsg(target, f"{sender}: {reply}")
                return

            if q_lower in ("log", "git log"):
                reply = await handle_git_log()
                await self.privmsg(target, f"{sender}: {reply}")
                return

            # Complex or arbitrary coding task
            await self.privmsg(target, f"{sender}: Working on task on m1.local (no limits: agy)...")
            reply = await handle_agent_dispatch(sender, query)
            await self.privmsg(target, f"{sender}: {reply}")


# ----------------------------------------------------------------------
# Unix Domain Socket IPC (Allows CLI or Antigravity session to talk as m1)
# ----------------------------------------------------------------------

async def handle_unix_client(reader, writer, client):
    data = await reader.read(65536)
    writer.close()
    await writer.wait_closed()

    decoded = data.decode("utf-8", errors="replace").strip()
    if not decoded:
        return
    parts = decoded.split(":", 2)
    if len(parts) == 3:
        _, channel, message = parts[0].strip(), parts[1].strip(), parts[2]
    elif len(parts) == 2:
        channel, message = parts[0].strip(), parts[1]
    else:
        channel, message = "#refineid", decoded

    log_daemon(f"[socket] Relaying message to {channel}: {message[:50]}...")
    await client.privmsg(channel, message)


def acquire_process_lock():
    global LOCK_FILE_OBJ
    try:
        f = open(LOCK_PATH, "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(f"{os.getpid()}\n")
        f.flush()
        LOCK_FILE_OBJ = f
        return True
    except (BlockingIOError, IOError):
        log_daemon(f"Another instance of m1-agent is already active. Exiting cleanly.")
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
        if os.path.exists(LOCK_PATH):
            try:
                os.remove(LOCK_PATH)
            except OSError:
                pass


async def main():
    global SERVER, PORT, USE_TLS

    parser = argparse.ArgumentParser(description="m1 IRC Agent Daemon")
    parser.add_argument("--server", default=SERVER, help="IRC Server host")
    parser.add_argument("--port", type=int, default=PORT, help="IRC Server port")
    parser.add_argument("--no-tls", action="store_true", help="Disable TLS")
    args = parser.parse_args()

    SERVER = args.server
    PORT = args.port
    if args.no_tls:
        USE_TLS = False

    if not acquire_process_lock():
        sys.exit(0)

    if os.path.exists(SOCKET_PATH):
        try:
            os.remove(SOCKET_PATH)
        except OSError:
            pass

    client = M1IrcClient()
    server = await asyncio.start_unix_server(
        lambda r, w: handle_unix_client(r, w, client),
        SOCKET_PATH
    )
    os.chmod(SOCKET_PATH, 0o777)
    log_daemon(f"m1 agent running. Socket at {SOCKET_PATH}, Chat log at {CHAT_LOG_FILE}")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def on_stop():
        log_daemon("Shutdown signal received, closing cleanly...")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, on_stop)
        except NotImplementedError:
            pass

    tasks = [
        asyncio.create_task(client.connect()),
        asyncio.create_task(server.serve_forever()),
    ]

    try:
        wait_task = asyncio.create_task(stop_event.wait())
        await asyncio.wait([wait_task] + tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await client.shutdown("m1 service reload")
        server.close()
        await server.wait_closed()
        release_process_lock()
        log_daemon("m1 agent shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        release_process_lock()
