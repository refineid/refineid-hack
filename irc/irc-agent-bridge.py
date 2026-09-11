#!/usr/bin/env python3
"""
irc-agent-bridge.py - Persistent local IRC agent bridge for Antigravity and Muse.
Connects two uninhibited agent bots (`antigravity` and `muse`) to 127.0.0.1:6667 (#refineid, #code-review).
Listens for user prompts (e.g. 'antigravity: ...' or 'muse: ...') and dispatches
responses back to IRC using full, uninhibited permissions:
  - agy --dangerously-skip-permissions --print
  - muse exec --yolo
"""

import asyncio
import os
import sys
import subprocess
import shutil

SERVER = "127.0.0.1"
PORT = 6667
CHANNELS = ["#refineid", "#code-review"]
SOCKET_PATH = "/tmp/irc-agent-bridge.sock"
LOG_FILE = "/tmp/irc-agent-bridge.log"

MUSE_BIN = os.environ.get("MUSE_BIN", "/Users/pk/.local/bin/muse")
AGY_BIN = os.environ.get("AGY_BIN", "/Users/pk/.local/bin/agy")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/Users/pk/src")


def log(msg):
    line = f"[bridge] {msg}\n"
    sys.stderr.write(line)
    sys.stderr.flush()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass


class IrcBot:
    def __init__(self, nick, realname):
        self.nick = nick
        self.realname = realname
        self.reader = None
        self.writer = None
        self.running = True

    async def connect(self):
        while self.running:
            try:
                log(f"[{self.nick}] Connecting to {SERVER}:{PORT}...")
                self.reader, self.writer = await asyncio.open_connection(SERVER, PORT)
                self.send(f"NICK {self.nick}")
                self.send(f"USER {self.nick} 0 * :{self.realname}")
                for ch in CHANNELS:
                    self.send(f"JOIN {ch}")
                log(f"[{self.nick}] Connected and joined {CHANNELS}")
                await self.listen_loop()
            except Exception as e:
                log(f"[{self.nick}] Connection error: {e}, reconnecting in 3s...")
                await asyncio.sleep(3)

    def send(self, line):
        if self.writer and not self.writer.is_closing():
            self.writer.write((line + "\r\n").encode("utf-8"))

    async def privmsg(self, target, msg):
        for line in msg.strip().splitlines():
            line = line.strip()
            if not line:
                continue
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
                log(f"[{self.nick}] Connection closed by remote")
                break
            raw = line.decode("utf-8", errors="replace").strip()
            if raw.startswith("PING "):
                token = raw[5:]
                self.send(f"PONG {token}")
                continue

            parts = raw.split(" ", 3)
            if len(parts) >= 4 and parts[1] == "PRIVMSG":
                sender = parts[0][1:].split("!", 1)[0]
                channel = parts[2]
                text = parts[3][1:] if parts[3].startswith(":") else parts[3]

                # Ignore messages sent by our own bots
                if sender in ("antigravity", "agv", "muse"):
                    continue

                await self.handle_message(sender, channel, text)

    async def handle_message(self, sender, channel, text):
        target = channel if channel.startswith("#") else sender
        trigger = f"{self.nick}:"
        alt_trigger = "agv:" if self.nick == "antigravity" else None

        matched = False
        query = ""
        if text.lower().startswith(trigger.lower()):
            query = text[len(trigger):].strip()
            matched = True
        elif alt_trigger and text.lower().startswith(alt_trigger.lower()):
            query = text[len(alt_trigger):].strip()
            matched = True

        if matched and query:
            log(f"[{self.nick}] Received query from {sender} in {target}: {query}")
            await self.privmsg(target, f"{sender}: Working on it (no limits: {self.nick})...")
            asyncio.create_task(self.dispatch_agent(sender, target, query))

    async def dispatch_agent(self, sender, target, query):
        try:
            if self.nick == "muse":
                # muse exec --yolo
                cmd = [MUSE_BIN, "exec", "--yolo", query]
            else:
                # agy --dangerously-skip-permissions --print
                cmd = [AGY_BIN, "--dangerously-skip-permissions", "--print", query]

            log(f"[{self.nick}] Executing: {' '.join(cmd)}")
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

            log(f"[{self.nick}] Finished query for {sender}, reply length: {len(reply)} chars")
            await self.privmsg(target, f"{sender}: {reply}")
        except Exception as e:
            log(f"[{self.nick}] Error executing agent query: {e}")
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
        log(f"[socket] Relaying message from {bot_name} to {channel}")
        await target_bot.privmsg(channel, message)


async def main():
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    agv_bot = IrcBot("antigravity", "Google Antigravity Agent")
    muse_bot = IrcBot("muse", "Muse Code Agent")
    bots = {"antigravity": agv_bot, "agv": agv_bot, "muse": muse_bot}

    server = await asyncio.start_unix_server(
        lambda r, w: handle_unix_client(r, w, bots),
        SOCKET_PATH
    )
    os.chmod(SOCKET_PATH, 0o777)
    log(f"IRC Agent Bridge running. Socket at {SOCKET_PATH}")

    await asyncio.gather(
        agv_bot.connect(),
        muse_bot.connect(),
        server.serve_forever()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
