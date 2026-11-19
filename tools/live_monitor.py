#!/usr/bin/env python3
"""
Read-only live mirror of the downloader's log files.

Writes timestamped events, progress milestones and 60-second state snapshots
to ``master.log``. With no flags it also renders a 2-second dashboard in the
terminal; with ``--headless`` it only writes the log (used by watchdog.py).

It never starts, stops or modifies the downloader.

    python tools/live_monitor.py [--headless]

Environment:
    NMMD_MODS_DIR   archive cache to count (default: ./downloads)
"""

import os
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("NMMD_MODS_DIR", BASE / "downloads"))
MASTER = BASE / "master.log"

SOURCES = {
    "SUPERVISOR": BASE / "supervisor.log",
    "DOWNLOADER": BASE / "output.log",
    "STDOUT": BASE / "downloader_stdout.log",
    "STDERR": BASE / "downloader_stderr.log",
}

REFRESH = 2
EVENT_LIMIT = 14
NOISE = re.compile(r"^\s*\d+%\s*\|")
FETCH = re.compile(r"(\d+)/(\d+)\s*\|\s*Fetching:\s*(.+)", re.I)
PROGRESS = re.compile(r"(\d+)%.*?\|\s*([\d.]+)/([\d.]+)\s*MB\s*(.*)")
MILESTONE = 10


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def master_write(text):
    try:
        with MASTER.open("a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")
    except Exception:
        pass


class Tailer:
    """Reads only appended bytes; recovers if the source file is recreated."""

    def __init__(self, path):
        self.path = path
        self.pos = 0

    def read(self):
        if not self.path.is_file():
            return []
        try:
            size = self.path.stat().st_size
            if size < self.pos:
                self.pos = 0
            with self.path.open("rb") as f:
                f.seek(self.pos)
                data = f.read()
                self.pos = f.tell()
            return [ln.strip() for ln in re.split(r"[\r\n]", data.decode("utf-8", "replace")) if ln.strip()]
        except Exception:
            return []


class State:
    def __init__(self):
        self.tails = {k: Tailer(v) for k, v in SOURCES.items()}
        self.events = []
        self.current = None
        self.current_index = ""
        self.progress = ""
        self.progress_at = 0
        self.pass_no = "-"
        self.target = 0
        self.poll_until = 0
        self.poll_seconds = 0
        self.status = "STARTING"
        self.successes = 0
        self.failures = 0
        self.resumes = 0
        self.last_milestone = -1
        self.last_snapshot = 0

    def event(self, source, line):
        msg = f"{stamp()} | {source:<10} | {line}"
        self.events.append(msg)
        del self.events[:-EVENT_LIMIT]
        master_write(msg)

    def consume(self):
        for name, tail in self.tails.items():
            for line in tail.read():
                self.handle(name, line)

    def handle(self, source, line):
        low = line.lower()

        if NOISE.match(line):
            self.progress = line
            self.progress_at = time.time()
            m = PROGRESS.search(line)
            if m:
                pct = int(m.group(1))
                if pct // MILESTONE != self.last_milestone:
                    self.last_milestone = pct // MILESTONE
                    self.event("PROGRESS", f"{pct}%  {m.group(2)}/{m.group(3)} MB  {m.group(4).strip()}")
            return

        m = FETCH.search(line)
        if m:
            self.current_index = f"{m.group(1)}/{m.group(2)}"
            self.current = m.group(3)
            self.progress = ""
            self.last_milestone = -1
            self.event(source, line)
            return

        if ("pass #" in low and "finished" in low) or "finished. processed" in low:
            self.status = "PASS COMPLETE"
            self.successes += 1
            self.event(source, line)
            return
        if "all " in low and "accounted for" in low:
            self.status = "COMPLETE"
            self.event(source, line)
            return
        if "pass #" in low:
            self.pass_no = re.search(r"pass #(\d+)", low).group(1)
            self.status = "RUNNING"
            self.event(source, line)
            return
        if "starting pass" in low or "starting download pass" in low:
            t = re.search(r"target:\s*(\d+)", low)
            if t:
                self.target = int(t.group(1))
            self.event(source, line)
            return
        if "sleeping" in low and "before next pass" in low:
            s = re.search(r"sleeping\s+(\d+)s", low)
            if s:
                self.poll_seconds = int(s.group(1))
                self.poll_until = time.time() + self.poll_seconds
            self.status = "POLLING"
            self.event(source, line)
            return
        if "already downloaded, skipping" in low:
            self.event(source, line)
            return
        if ("cannot resume" in low or "size mismatch" in low or "oversized" in low
                or "interrupted while downloading" in low):
            self.failures += 1
            if "interrupted" in low:
                self.resumes += 1
            self.event(source, "!! " + line)
            return
        if "failed attempt" in low or "failed to fetch" in low or "timeoutexceeded" in low:
            self.failures += 1
            self.event(source, "!! " + line)
            return
        if "final cache count" in low or "cache now holds" in low or "cache complete" in low:
            self.event(source, line)
            return
        if "stopped by user" in low or "terminating" in low:
            self.status = "STOPPED"
            self.event(source, line)
            return

        if "requirement already satisfied" in low or "defaulting to user" in low:
            return
        if source in ("SUPERVISOR", "DOWNLOADER", "STDOUT"):
            self.event(source, line)

    def cache_count(self):
        try:
            return len([f for f in CACHE.iterdir() if f.is_file()])
        except Exception:
            return 0

    def render(self):
        os.system("cls" if os.name == "nt" else "clear")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if time.time() < self.poll_until:
            left = int(self.poll_until - time.time())
            poll = f"POLLING - next pass in {left}s / {self.poll_seconds}s"
        else:
            poll = "-"

        m = PROGRESS.search(self.progress) if self.progress else None
        if m:
            pctbar = f"{m.group(1)}%  {m.group(2)}/{m.group(3)} MB   {m.group(4).strip()}"
            age = int(time.time() - self.progress_at)
            if age > 20:
                pctbar += f"   [stale {age}s]"
        else:
            pctbar = "waiting for CDN..."

        print("=" * 90)
        print(f" NEXUS DOWNLOAD MONITOR                                 {now}")
        print("=" * 90)
        print(f" Status      : {self.status}")
        print(f" Pass        : {self.pass_no}")
        print(f" Poll        : {poll}")
        print(f" Cache       : {self.cache_count()} / {self.target or '?'} archives")
        print(f" Session     : {self.successes} passes | {self.failures} issues | {self.resumes} resumes")
        print("-" * 90)
        if self.current:
            print(f" Downloading : [{self.current_index}] {self.current}")
        else:
            print(" Downloading : (idle - nothing in flight)")
        print(f" Progress    : {pctbar}")
        print("-" * 90)
        print(" RECENT EVENTS")
        for e in self.events[-EVENT_LIMIT:]:
            print("  " + e[:160])
        print("-" * 90)
        print(f" Master log  : {MASTER}")
        print(" Refresh 2s | Ctrl+C closes only this window - downloads keep running")
        print("=" * 90)

    def snapshot(self):
        if time.time() - self.last_snapshot < 60:
            return
        self.last_snapshot = time.time()
        master_write(
            f"{stamp()} | STATE      | status={self.status} pass={self.pass_no} "
            f"cache={self.cache_count()}/{self.target} current={self.current!r} "
            f"progress={self.progress.strip()!r} issues={self.failures} resumes={self.resumes}"
        )


def main():
    headless = "--headless" in sys.argv

    global _instance_lock
    _instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _instance_lock.bind(("127.0.0.1", 47912))
    except OSError:
        print("A log mirror is already running. Exiting.")
        sys.exit(1)

    master_write("=" * 90)
    master_write(f"{stamp()} | MONITOR STARTED - live log mirror ({'headless' if headless else 'window'})")
    master_write("=" * 90)

    state = State()
    try:
        while True:
            state.consume()
            state.snapshot()
            if not headless:
                try:
                    state.render()
                except Exception as exc:
                    master_write(f"{stamp()} | RENDER ERROR: {exc}")
            time.sleep(REFRESH)
    except KeyboardInterrupt:
        master_write(f"{stamp()} | MONITOR CLOSED (downloads unaffected)")
    except Exception as exc:
        master_write(f"{stamp()} | MONITOR CRASHED: {exc}")


if __name__ == "__main__":
    main()
