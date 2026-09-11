#!/usr/bin/env python3
"""
irc-agent-bridge.py - Persistent local IRC agent bridge for Antigravity and Muse.
Connects two agent bots (`antigravity` and `muse`) to 127.0.0.1:6667 (#refineid, #code-review).
Listens for user prompts (e.g. 'antigravity: ...' or 'muse: ...') and dispatches
responses back to IRC. Also provides a local UNIX socket to broadcast discussion turns.
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

MUSE_BIN = os.environ.get("MUSE_BIN", "/Users/pk/.local/bin/muse")
AGY_BIN = os.environ.get("AGY_BIN", "/Users/pk/.local/bin/agy")


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
                self.reader, self.writer = await asyncio.open_connection(SERVER, PORT)
                self.send(f"NICK {self.nick}")
                self.send(f"USER {self.nick} 0 * :{self.realname}")
                for ch in CHANNELS:
                    self.send(f"JOIN {ch}")
                await self.listen_loop()
            except Exception as e:
                print(f"[{self.nick}] Connection error: {e}, reconnecting in 5s...", file=sys.stderr)
                await asyncio.sleep(5)

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
                await asyncio.sleep(0.2)
            self.send(f"PRIVMSG {target} :{line}")
            await asyncio.sleep(0.15)

    async def listen_loop(self):
        while self.running:
            line = await self.reader.readline()
            if not line:
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
            await self.privmsg(target, f"{sender}: On it. Thinking...")
            asyncio.create_task(self.dispatch_agent(sender, target, query))

    async def dispatch_agent(self, sender, target, query):
        loop = asyncio.get_running_loop()
        try:
            if self.nick == "muse":
                proc = await asyncio.create_subprocess_exec(
                    MUSE_BIN, "exec", "--yolo", query,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                out_text = stdout.decode("utf-8", errors="replace")
                # Remove runtime banner lines
                lines = [l for l in out_text.splitlines() if not l.startswith("muse: workspace")]
                reply = "\n".join(lines).strip()
            else:
                proc = await asyncio.create_subprocess_exec(
                    AGY_BIN, "--dangerously-skip-permissions", "--print", query,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                reply = stdout.decode("utf-8", errors="replace").strip()

            if not reply:
                reply = "(no output produced)"
            await self.privmsg(target, f"{sender}: {reply}")
        except Exception as e:
            await self.privmsg(target, f"{sender}: Error executing agent query: {e}")


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
    print(f"IRC Agent Bridge running. Socket at {SOCKET_PATH}", file=sys.stderr)

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
