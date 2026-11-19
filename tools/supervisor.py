#!/usr/bin/env python3
"""
Optional unattended runner for the Wabbajack-aware downloader.

Runs repeated idempotent passes:
  * archives already in the output cache are skipped by the downloader
  * .part files are resumed, never restarted from zero
  * after a pass it waits POLL_INTERVAL and checks again
  * stops once the cache contains every archive in the mod list

Usage:
    python tools/supervisor.py [poll_seconds]

Environment overrides:
    NMMD_MODLIST      path to the download list (default: ./download_list.txt)
    NMMD_MODS_DIR     final archive cache (default: ./downloads)
    NMMD_POLL_INTERVAL  seconds between passes (default: 300)
"""

import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE / "config.json"
MODLIST_PATH = Path(os.environ.get("NMMD_MODLIST", BASE / "download_list.txt"))
DOWNLOADER_PATH = BASE / "Nexus Mods Mod Downloader.py"
STATUS_PATH = BASE / "download_status.json"
WABBAJACK_CACHE = Path(os.environ.get("NMMD_MODS_DIR", BASE / "downloads"))
POLL_INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("NMMD_POLL_INTERVAL", 300))
RUN_TIMEOUT = 21600  # 6 hours per pass; partials are preserved on timeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(BASE / "supervisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("supervisor")


def load_config():
    if not CONFIG_PATH.exists():
        logger.error("config.json missing: %s", CONFIG_PATH)
        return None
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for key in ("hide", "apikey", "firefox"):
        if key not in cfg:
            logger.error("config.json missing key: %s", key)
            return None
    if not os.path.isdir(cfg["firefox"]):
        logger.error("Firefox profile not found: %s", cfg["firefox"])
        return None
    if not cfg["apikey"] or "Find your api key" in cfg["apikey"]:
        logger.error("Nexus API key is still a placeholder in config.json")
        return None

    # Warn early if the copied profile has no Nexus session cookies.
    cookies = Path(cfg["firefox"]) / "cookies.sqlite"
    if cookies.exists():
        import shutil
        import sqlite3
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "c.sqlite"
        try:
            shutil.copy2(cookies, tmp)
            con = sqlite3.connect(tmp)
            count = con.execute(
                "select count(*) from moz_cookies where host like '%nexusmods%'"
            ).fetchone()[0]
            con.close()
            if count == 0:
                logger.warning("Firefox profile has NO nexusmods.com cookies - browser downloads will fail")
        except Exception as exc:
            logger.warning("Could not inspect Firefox cookies: %s", exc)
    else:
        logger.warning("No cookies.sqlite in Firefox profile - log into nexusmods.com there first")
    return cfg


def count_archives():
    lines = [x.strip() for x in MODLIST_PATH.read_text(encoding="utf-8").splitlines() if x.strip()]
    return max(0, len(lines) - 1)  # first line is the game domain


def cache_count():
    if not WABBAJACK_CACHE.is_dir():
        return 0
    return len([x for x in WABBAJACK_CACHE.iterdir() if x.is_file()])


def read_status():
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_once():
    logger.info("=" * 60)
    logger.info("Starting pass | target: %d archives | already cached: %d",
                count_archives(), cache_count())
    logger.info("=" * 60)
    STATUS_PATH.unlink(missing_ok=True)

    env = os.environ.copy()
    env["NMMD_MODS_DIR"] = str(WABBAJACK_CACHE)
    env["PYTHONUNBUFFERED"] = "1"

    # Redirect to files instead of PIPE: tqdm writes megabytes to stderr and
    # an unread pipe would deadlock the child.
    with (BASE / "downloader_stdout.log").open("wb") as out, \
            (BASE / "downloader_stderr.log").open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, str(DOWNLOADER_PATH)],
            stdin=subprocess.PIPE, stdout=out, stderr=err,
            cwd=BASE, env=env,
        )
        try:
            proc.stdin.write((str(MODLIST_PATH) + "\n").encode("utf-8"))
            proc.stdin.flush()
            proc.stdin.close()
        except Exception as exc:
            logger.error("Could not send modlist path to child: %s", exc)

        try:
            return proc.wait(timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            logger.warning("Pass exceeded %ds - terminating; partial files are preserved", RUN_TIMEOUT)
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            return -1


def main():
    # A bound local port is a self-cleaning single-instance lock.
    global _instance_lock
    _instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _instance_lock.bind(("127.0.0.1", 47911))
    except OSError:
        print("Another download supervisor is already running. Exiting.")
        sys.exit(1)

    logger.info("Nexus download supervisor")
    logger.info("  modlist      : %s (%d archives)", MODLIST_PATH, count_archives())
    logger.info("  output cache : %s (%d files now)", WABBAJACK_CACHE, cache_count())
    logger.info("  poll interval: %ds", POLL_INTERVAL)
    logger.info("  Firefox must be logged into nexusmods.com and CLOSED before starting")

    if load_config() is None:
        sys.exit(1)
    if not MODLIST_PATH.is_file():
        logger.error("modlist missing: %s", MODLIST_PATH)
        sys.exit(1)

    expected = count_archives()
    iteration = 0
    try:
        while True:
            iteration += 1
            logger.info("Pass #%d starting", iteration)
            rc = run_once()
            status = read_status()

            if status and status.get("done"):
                processed = status.get("processed", 0)
                logger.info("Pass #%d finished: processed=%d failed=%s exit=%s",
                            iteration, processed, status.get("failed"), rc)
                if not status.get("failed") and processed >= expected:
                    logger.info("All %d archives accounted for. Done.", expected)
                    break
                if not status.get("failed"):
                    have = cache_count()
                    logger.info("Cache now holds %d files (need %d)", have, expected)
                    if have >= expected:
                        logger.info("Cache complete. Done.")
                        break
            else:
                logger.warning("Pass #%d ended without a clean status (exit=%s) - will retry", iteration, rc)

            logger.info("Sleeping %ds before next pass...", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Stopped by user. Partial files are safe; rerun to resume.")
    finally:
        logger.info("Final cache count: %d / %d", cache_count(), expected)


if __name__ == "__main__":
    main()
