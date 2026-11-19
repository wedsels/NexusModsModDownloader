#!/usr/bin/env python3
"""
Nexus Mods Mod Downloader - Wabbajack-aware fork
================================================

Downloads mods from a Nexus collection URL or a plain-text mod list.

Input formats (first non-empty line is always the Nexus game domain):
    skyrimspecialedition
    16231;                              all main files for mod 16231
    88943:Fade's Equipment              one matching main file
    5127;Patch A;Patch B                all main files plus named optional files
    https://.../download                direct download URL
    47087|735524|Archive-47087-...zip   EXACT file used by a Wabbajack modlist

Wabbajack-specific behaviour (all optional):
  * ``modID|fileID|archiveName`` fetches the exact archive, no API guessing.
  * Cached archives are detected and skipped before any browser navigation.
  * Downloads are written to ``<name>.part`` and resumed with HTTP Range;
    a truncated or corrupt stream is never promoted into the final cache.
  * ``expected_sizes.json`` (optional, written by tools/wabbajack_converter.py)
    is used to verify the final byte count against the Wabbajack manifest.
  * ``NMMD_MODS_DIR`` can point finished archives at an existing mod cache.
  * Unlike upstream, unrecognised files in the output folder are NEVER deleted.

Based on https://github.com/wedsels/NexusModsModDownloader (no upstream license).
"""

import ctypes
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import traceback
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from tqdm import tqdm

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
LOG_PATH = BASE / "output.log"
CRASH_PATH = BASE / "crash.txt"
STATUS_PATH = BASE / "download_status.json"
EXPECTED_SIZES_PATH = BASE / "expected_sizes.json"
PROFILE_DIR = BASE / "profile"
DOWNLOAD_DIR = BASE / "downloads"

all = "!All!"

# Scrubbed base name -> exact byte count, loaded from expected_sizes.json.
EXPECTED_SIZES = {}

logger = logging.getLogger("NMMD")
modit = 0
ln = 0
failed = False
session = requests.Session()
processedfiles = []
page = None


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def fold(directory, clean=True):
    def error(func, path, _):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    if clean and os.path.exists(directory):
        shutil.rmtree(directory, onexc=error)
    os.makedirs(directory, exist_ok=True)


def retry(func, *args, **kwargs):
    tries = 5
    for attempt in range(tries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.info("%s\nFailed attempt %d/%d", e, attempt + 1, tries)
            time.sleep(1 + attempt)

    global failed
    failed = True
    return None


def goto(url, again=True):
    def go():
        clear()
        logger.info("Navigating: %s", url)
        page.goto(url, wait_until="domcontentloaded")

    if again:
        retry(go)
    else:
        go()


def scrub(name):
    """Match the filename normalisation used for cache/size lookups."""
    return re.sub(r'[\\/:*?"<>|.]', "", name).strip()


def download(event, filename=None):
    """Run a browser event that yields a DownloadInfo, then save it resumably."""
    def checkname():
        nonlocal filename
        filename = scrub(filename)
        processedfiles.append(filename)
        if filename in modlist:
            clear()
            logger.info("%d/%d | Already Downloaded, Skipping: %s", modit, ln, filename)
            return True
        return False

    if filename and checkname():
        return

    ret = retry(event)
    if not ret:
        if filename is not None:
            logger.info("You may not be logged into nexus mods, or this file is not publicly visible")
        return

    file = ret.value
    file.cancel()

    if not filename:
        filename = os.path.splitext(file.suggested_filename)[0]
        if checkname():
            return

    filename = filename + os.path.splitext(file.suggested_filename)[1]
    clear()
    logger.info("%d/%d | Fetching: %s", modit, ln, filename)

    # Never write directly to the final name: a crash must leave a usable .part.
    part = os.path.join(downloads, filename + ".part")
    legacy = os.path.join(downloads, filename)

    # Migrate a partial written by the pre-.part version of this script.
    if os.path.exists(legacy) and not os.path.exists(part):
        try:
            os.replace(legacy, part)
        except Exception:
            pass

    existing = os.path.getsize(part) if os.path.exists(part) else 0
    expected = EXPECTED_SIZES.get(scrub(os.path.splitext(filename)[0]))

    if expected and existing >= expected:
        if existing == expected:
            shutil.move(part, os.path.join(mods, filename))
            return
        logger.info("Partial for %s is oversized (%d > %d); leaving it untouched", filename, existing, expected)
        return

    headers = {"Range": f"bytes={existing}-"} if existing > 0 else {}
    response = session.get(file.url, allow_redirects=True, stream=True, timeout=60, headers=headers)

    if existing > 0 and response.status_code != 206:
        # A 200 means the server ignored Range; appending would corrupt the file.
        logger.info(
            "Cannot resume %s: Range bytes=%d- returned %d instead of 206. Partial preserved.",
            filename, existing, response.status_code,
        )
        response.close()
        return

    if response.status_code not in (200, 206):
        logger.info("Failed to fetch %s\nStatus: %d", filename, response.status_code)
        response.close()
        return

    mb = 1024 * 1024
    total_bytes = expected or (int(response.headers.get("content-length", 0)) + existing)
    written = 0

    try:
        with open(part, "ab" if existing > 0 else "wb") as f:
            with tqdm(
                total=total_bytes / mb,
                initial=existing / mb,
                unit="MB",
                bar_format="{l_bar}{bar}| {n:.1f}/{total:.1f} {unit} {rate_fmt} {remaining}",
            ) as pbar:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
                        pbar.update(len(chunk) / mb)
            f.flush()
            os.fsync(f.fileno())
    except Exception as exc:
        logger.info(
            "Interrupted while downloading %s: %s. Kept %d bytes in partial file.",
            filename, exc, existing + written,
        )
        response.close()
        raise  # retry() gets a fresh CDN URL and continues from the exact byte

    response.close()
    final_size = existing + written

    if expected and final_size != expected:
        logger.info("Size mismatch for %s: got %d, expected %d. Partial kept for resume.",
                    filename, final_size, expected)
        return

    shutil.move(part, os.path.join(mods, filename))


def url_download(url):
    if not url or not url.startswith("https://"):
        return

    def down():
        with page.expect_download() as info:
            try:
                goto(url, False)
            except Exception:
                pass
            return info

    retry(download, down)


def id_download(mod_id, file_id, filename=None):
    def down():
        goto(f"https://www.nexusmods.com/{game}/mods/{mod_id}?tab=files&file_id={file_id}")

        # Nexus renders download controls as a <mod-file-download> web component.
        # The old CustomEvent('slowDownload') dispatch is dead; the visible
        # "Slow download" button inside its shadow DOM must be clicked.
        with page.expect_download(timeout=60000) as info:
            button = page.locator("mod-file-download").first.locator(
                "button", has_text="Slow download"
            )
            button.wait_for(state="visible", timeout=30000)
            button.click(timeout=15000)

        return info

    retry(download, down, filename)


def api(mod_id, cat="", file_id=""):
    def request():
        endpoint = (f"https://api.nexusmods.com/v1/games/{game}/mods/{mod_id}/files"
                    f"{'.json?category=' + cat if file_id == '' else '/' + file_id + '.json'}")
        response = session.get(endpoint, headers={"accept": "application/json", "apikey": apikey})
        if response.status_code == 200:
            return response.json()
        raise Exception(f"Failed to communicate with Nexus API for mod {mod_id}:{file_id}")

    return retry(request)


def main():
    global modit, mods, modlist, game, apikey, downloads

    if path.endswith(".txt"):
        for line in data:
            modit += 1
            line = line.strip()
            if not line:
                continue
            if line.startswith("https://"):
                url_download(line)
                continue

            # Wabbajack format: modID|fileID|archiveName
            if "|" in line:
                parts = line.split("|", 2)
                if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                    id_download(parts[0], parts[1], os.path.splitext(parts[2])[0])
                    continue

            split = line.split(";")
            first = split[0].split(":")
            mod_id = first[0]
            wanted = first[1:]

            if not wanted and ":" not in split[0]:
                wanted = [all]

            optional = split[1:]
            if optional and optional[0] == "":
                optional[0] = all

            def files(kind, name):
                if not kind:
                    return
                result = api(mod_id, name)
                if not result:
                    return
                for wanted_name in kind:
                    for entry in result["files"]:
                        if wanted_name == all or wanted_name.lower() == entry["name"].lower():
                            id_download(mod_id, entry["file_id"],
                                        os.path.splitext(entry["file_name"])[0])

            files(wanted, "main")
            files(optional, "optional")
            files(optional, "update")

    elif path.startswith("https://"):
        for item in data["externalResources"]:
            modit += 1
            url_download(item["resourceUrl"])
        for item in data["modFiles"]:
            modit += 1
            file_info = item["file"]
            mod = file_info["mod"]
            id_download(mod["modId"], item["fileId"],
                        f'{file_info["name"]}-{mod["modId"]}-{item["fileId"]}'
                        f'-{mod["version"]}-{file_info["version"]}')

    # Intentionally no "delete unrecognised files in output folder" step:
    # that upstream behaviour destroys pre-existing mod caches.


def load_config():
    while True:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.loads(re.sub(r"\\+", "/", f.read()))

        hide = config["hide"]
        if not isinstance(hide, bool):
            clear()
            input(f'Invalid "hide" value in {CONFIG_PATH}.\nPress any key to continue')
            continue

        api_key = config["apikey"]
        if session.get("https://api.nexusmods.com/v1/users/validate.json",
                       headers={"accept": "application/json", "apikey": api_key}).status_code != 200:
            clear()
            input(f'Invalid "apikey" value in {CONFIG_PATH}.\nPress any key to continue')
            continue

        firefox = config["firefox"].rstrip("/\\") + "/"
        if not os.path.isdir(firefox):
            clear()
            input(f'Invalid "firefox" value in {CONFIG_PATH}.\nPress any key to continue')
            continue

        return hide, api_key, firefox


def setup_logging():
    if LOG_PATH.exists():
        LOG_PATH.unlink()
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(logging.FileHandler(LOG_PATH, encoding="utf-8"))
    logger.addHandler(logging.StreamHandler(sys.stdout))


def get_input(browser_page):
    global path, data, game, ln
    while True:
        clear()
        path = input("Drop or paste a url to a collection or path to a .txt file, then press enter\n").strip().strip('"')

        if path.startswith("https://www.nexusmods.com/games/") and "/collections/" in path:
            try:
                if not path.endswith("/mods"):
                    path += "/mods"
                with browser_page.expect_response(
                    lambda r: r.request.headers.get("x-graphql-operationname") == "CollectionRevisionMods"
                    and r.status == 200
                ) as response_info:
                    goto(path)
                response = response_info.value
                if response.status != 200:
                    continue
                name = browser_page.text_content(
                    ".typography-heading-md.sm\\:typography-heading-lg.text-neutral-strong.break-words.font-semibold"
                )
                mods = f"./{name}/"
                collection = response.json()["data"]["collectionRevision"]
                ln = len(collection["externalResources"]) + len(collection["modFiles"])
                game = re.search(r"/games/([^/]+)/", path).group(1)
                data = collection
                break
            except Exception as e:
                clear()
                logger.info("%s\nFailed to parse collection: %s", e, path)
                continue

        if os.path.isfile(path) and path.endswith(".txt"):
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            game = lines[0].strip()
            data = lines[1:]
            ln = len(data)
            mods = f"./{os.path.splitext(os.path.basename(path))[0]}/"
            break

        clear()
        logger.info("Invalid: %s", path)

    mods = scrub(mods)

    # Optional: send finished archives straight into an existing mod cache.
    override = os.environ.get("NMMD_MODS_DIR")
    if override:
        mods = override.replace("\\", "/").rstrip("/") + "/"

    if not os.path.isdir(mods):
        os.makedirs(mods, exist_ok=True)

    modlist = []
    for entry in os.listdir(mods):
        modlist.append(scrub(os.path.splitext(entry)[0]))

    return mods, modlist, game, ln


def run():
    global game, ln, mods, modlist, downloads, page, apikey

    if os.name == "nt":
        os.system("mode con: cols=140 lines=3")
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)

    setup_logging()

    if EXPECTED_SIZES_PATH.is_file():
        try:
            EXPECTED_SIZES.update(json.loads(EXPECTED_SIZES_PATH.read_text(encoding="utf-8")))
            logger.info("Loaded %d expected archive sizes", len(EXPECTED_SIZES))
        except Exception as e:
            logger.info("Could not load expected_sizes.json: %s", e)

    logger.info("Setting up...")
    hide, apikey, firefox = load_config()

    fold(PROFILE_DIR, False)
    downloads = str(DOWNLOAD_DIR)
    fold(downloads, False)

    for name in ("cookies.sqlite", "extensions.json", "extension-settings.json",
                 "extension-preferences.json", "extensions"):
        source = os.path.join(firefox, name)
        target = os.path.join(str(PROFILE_DIR), name)
        try:
            if os.path.isfile(source):
                shutil.copy(source, target)
            elif os.path.isdir(source):
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                logger.info("Error: could not find %s at the configured Firefox profile", name)
        except Exception as e:
            logger.info("Could not copy %s: %s", name, e)

    with sync_playwright() as playwright:
        context = playwright.firefox.launch_persistent_context(
            str(PROFILE_DIR),
            headless=hide,
            accept_downloads=True,
            downloads_path=downloads,
            viewport={"width": 1920, "height": 1080},
        )
        Stealth().apply_stealth_sync(context)
        page = context.pages[0] if context.pages else context.new_page()
        mods, modlist, game, ln = get_input(page)
        main()

    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)

    clear()
    try:
        STATUS_PATH.write_text(json.dumps({
            "done": True,
            "failed": failed,
            "processed": len(processedfiles),
            "mods_dir": mods,
            "log": str(LOG_PATH),
        }, indent=2), encoding="utf-8")
    except Exception:
        pass

    logger.info("Finished. Processed %d files. Failed: %s", len(processedfiles), failed)
    logger.info("Mods directory: %s", mods)


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        CRASH_PATH.write_text(f"Failure:\n{e}\n\n{traceback.format_exc()}", encoding="utf-8")
        logger.info("Crashed - see %s", CRASH_PATH)
        if os.name == "nt":
            os.startfile(os.path.abspath(CRASH_PATH))
        raise
