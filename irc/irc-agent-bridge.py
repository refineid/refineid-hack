#!/usr/bin/env python3
"""
irc-agent-bridge.py - Persistent local IRC agent bridge for Antigravity (ag) and Muse.
Connects two uninhibited agent bots (`ag` and `muse`) to 127.0.0.1:6667 (#refineid).
Listens for user prompts (e.g. "ag: ..." or "muse: ...") and dispatches
responses back to IRC using full, uninhibited permissions:
  - agy --dangerously-skip-permissions --print
  - muse exec --yolo

Logs all channel messages and events to irc/logs/channel-refineid.log for agents to read.
"""

import asyncio
import datetime
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

    # Maintain convenience symlink in /tmp
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


class IrcBot:
    def __init__(self, nick, realname, is_logger=False):
        self.nick = nick
        self.realname = realname
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

                # Only the designated logger logs incoming channel chat to prevent duplicate lines
                if self.is_logger and channel.startswith("#"):
                    # Don\'t duplicate bot messages already logged in privmsg()
                    if sender not in ("ag", "antigravity", "agv", "muse"):
                        log_chat(f"<{sender}> {text}")

                # Ignore triggers from our own bots to avoid infinite loops
                if sender in ("ag", "antigravity", "agv", "muse"):
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

        # Triggers supported for this bot
        if self.nick == "ag":
            triggers = ["ag:", "ag,", "ag ", "@ag ", "antigravity:", "agv:"]
        elif self.nick == "muse":
            triggers = ["muse:", "muse,", "muse ", "@muse "]
        else:
            triggers = [f"{self.nick}:", f"{self.nick} "]

        matched = False
        query = ""
        lower_text = text.lower()
        for trig in triggers:
            if lower_text.startswith(trig):
                query = text[len(trig):].strip()
                matched = True
                break

        if matched and query:
            log_daemon(f"[{self.nick}] Received query from {sender} in {target}: {query}")
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
                # muse exec --yolo
                cmd = [MUSE_BIN, "exec", "--yolo", full_prompt]
            else:
                # agy --dangerously-skip-permissions --print
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

    # Bot instances: \x27ag\x27 (Antigravity) and \x27muse\x27 (Muse Code)
    # \x27ag\x27 acts as the primary channel logger
    ag_bot = IrcBot("ag", "Google Antigravity Agent", is_logger=True)
    muse_bot = IrcBot("muse", "Muse Code Agent", is_logger=False)

    bots = {
        "ag": ag_bot,
        "antigravity": ag_bot,
        "agv": ag_bot,
        "muse": muse_bot,
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
        server.serve_forever()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
