"""Nexus Mods batch mod downloader.

Downloads mods from Nexus Mods either from a Nexus collection URL or from a
local text file describing mod IDs / file IDs / direct download URLs.

Usage:
    python nexus_downloader.py [--config config.json] [--source <url|file.txt>]

If --source is omitted, the user is prompted interactively.

The text file format is::

    <gameDomain>
    <modId>[:mainName1:mainName2...][;optionalName1;optionalName2...]
    https://www.nexusmods.com/.../download_link_or_collection
    ...

Use the literal string ``!All!`` (or empty after ``;``) to grab every file in a
category.
"""

from __future__ import annotations

import argparse
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TypeVar

try:
    import requests
    from playwright.sync_api import sync_playwright, Page, BrowserContext, Download
    from playwright_stealth import Stealth
    from tqdm import tqdm
except ImportError as exc:  # pragma: no cover - import-time guard
    sys.stderr.write(
        f"Missing dependency: {exc.name}.\n"
        "Install requirements with:\n"
        "    pip install -r requirements.txt\n"
        "    python -m playwright install firefox\n"
    )
    raise SystemExit(1)

ALL_MARKER = "!All!"
ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / "output.log"
CRASH_FILE = ROOT / "crash.txt"
PROFILE_DIR = ROOT / "profile"
DOWNLOADS_DIR = ROOT / "downloads"
DEFAULT_CONFIG = ROOT / "config.json"

INVALID_FS_CHARS = re.compile(r'[\\/:*?"<>|]')
NEXUS_COLLECTION_RE = re.compile(
    r"^https://www\.nexusmods\.com/games/([^/]+)/collections/", re.IGNORECASE
)
GAME_DOMAIN_RE = re.compile(r"/games/([^/]+)/", re.IGNORECASE)

MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 1.0
DOWNLOAD_CHUNK = 64 * 1024
MB = 1024 * 1024

log = logging.getLogger("nexus")

T = TypeVar("T")

class DownloaderError(Exception):
    """Base error for the downloader."""


class ConfigError(DownloaderError):
    """Raised when configuration is invalid."""


class RetryError(DownloaderError):
    """Raised when an operation has exhausted its retry budget."""


@dataclass(frozen=True)
class Config:
    hide: bool
    apikey: str
    firefox_profile: Path

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Config file is not valid JSON: {exc}") from exc

        try:
            hide = data["hide"]
            apikey = data["apikey"]
            firefox = data["firefox"]
        except KeyError as exc:
            raise ConfigError(f"Missing required config key: {exc.args[0]}") from exc

        if not isinstance(hide, bool):
            raise ConfigError('Config key "hide" must be a boolean.')
        if not isinstance(apikey, str) or not apikey or apikey == "API KEY":
            raise ConfigError('Config key "apikey" must be a valid Nexus Mods API key.')
        if not isinstance(firefox, str) or not firefox:
            raise ConfigError('Config key "firefox" must be a string path.')

        firefox_path = Path(firefox.replace("\\", "/")).expanduser()
        if not firefox_path.is_dir():
            raise ConfigError(
                f'Config key "firefox" must point to an existing directory: {firefox_path}'
            )
        return cls(hide=hide, apikey=apikey, firefox_profile=firefox_path)


def setup_logging() -> None:
    if LOG_FILE.exists():
        try:
            LOG_FILE.unlink()
        except OSError:
            pass

    log.setLevel(logging.INFO)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    log.addHandler(file_handler)
    log.addHandler(stream_handler)


def sanitize_filename(name: str) -> str:
    """Strip characters that are illegal in Windows/Posix filenames."""
    return INVALID_FS_CHARS.sub("", name).strip().rstrip(".")


def _on_rm_error(func: Callable[..., Any], path: str, _exc_info: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def ensure_dir(path: Path, *, clean: bool = False) -> None:
    """Ensure ``path`` exists. If ``clean`` is True, remove existing contents."""
    if clean and path.exists():
        shutil.rmtree(path, onexc=_on_rm_error)
    path.mkdir(parents=True, exist_ok=True)


def retry(
    func: Callable[..., T],
    *args: Any,
    tries: int = MAX_RETRIES,
    label: str = "",
    **kwargs: Any,
) -> T:
    """Retry ``func`` with linear back-off, raising ``RetryError`` on failure."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, tries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - we want the broadest catch
            last_exc = exc
            log.warning(
                "%sAttempt %d/%d failed: %s",
                f"[{label}] " if label else "",
                attempt,
                tries,
                exc,
            )
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RetryError(f"All {tries} retries failed for {label or func.__name__}") from last_exc

class NexusAPI:
    """Thin wrapper around the Nexus Mods JSON API."""

    BASE = "https://api.nexusmods.com/v1"

    def __init__(self, apikey: str, session: Optional[requests.Session] = None) -> None:
        self._apikey = apikey
        self.session = session or requests.Session()
        self.session.headers.update({"accept": "application/json", "apikey": apikey})

    def validate(self) -> bool:
        try:
            response = self.session.get(f"{self.BASE}/users/validate.json", timeout=15)
        except requests.RequestException as exc:
            log.error("Could not reach Nexus Mods API: %s", exc)
            return False
        return response.status_code == 200

    def list_files(self, game: str, mod_id: str, category: str = "") -> dict:
        url = f"{self.BASE}/games/{game}/mods/{mod_id}/files.json"
        params = {"category": category} if category else None
        return retry(self._get_json, url, params, label=f"api list {mod_id}/{category}")

    def _get_json(self, url: str, params: Optional[dict] = None) -> dict:
        response = self.session.get(url, params=params, timeout=30)
        if response.status_code != 200:
            raise DownloaderError(
                f"Nexus API request failed ({response.status_code}): {url}"
            )
        return response.json()


@dataclass
class DownloadJob:
    game: str
    target_dir: Path
    items: list  # list of dicts describing what to download

class NexusDownloader:
    def __init__(
        self,
        config: Config,
        api: NexusAPI,
        page: Page,
        downloads_dir: Path,
    ) -> None:
        self.config = config
        self.api = api
        self.page = page
        self.session = api.session
        self.downloads_dir = downloads_dir
        self.processed: set[str] = set()
        self.failed_count = 0
        self.current_index = 0
        self.total = 0
        self.target_dir: Optional[Path] = None
        self.existing_filenames: set[str] = set()

    # -- navigation --------------------------------------------------------

    def goto(self, url: str, *, retries: bool = True) -> None:
        def _go() -> None:
            log.info("Navigating: %s", url)
            self.page.goto(url, wait_until="networkidle", timeout=30_000)

        if retries:
            retry(_go, label=f"goto {url}")
        else:
            _go()

    # -- file handling -----------------------------------------------------

    def _is_already_downloaded(self, filename_no_ext: str) -> bool:
        if filename_no_ext in self.existing_filenames:
            log.info(
                "%d/%d | Already downloaded, skipping: %s",
                self.current_index,
                self.total,
                filename_no_ext,
            )
            return True
        return False

    def _stream_to_disk(self, url: str, dest: Path) -> None:
        existing = dest.stat().st_size if dest.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        response = self.session.get(
            url, allow_redirects=True, stream=True, timeout=60, headers=headers
        )
        if response.status_code not in (200, 206):
            raise DownloaderError(
                f"Failed to download {dest.name}: HTTP {response.status_code}"
            )

        total = int(response.headers.get("content-length", 0)) + existing
        mode = "ab" if existing else "wb"
        with open(dest, mode) as fh, tqdm(
            total=total / MB,
            initial=existing / MB,
            unit="MB",
            bar_format="{l_bar}{bar}| {n:.1f}/{total:.1f} {unit} {rate_fmt} {remaining}",
        ) as pbar:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK):
                if not chunk:
                    continue
                fh.write(chunk)
                pbar.update(len(chunk) / MB)

    def _download_from_event(
        self,
        trigger: Callable[[], Any],
        preferred_name: Optional[str] = None,
    ) -> None:
        """Trigger a download in Playwright and stream the file via requests."""
        if preferred_name:
            sanitized = sanitize_filename(preferred_name)
            self.processed.add(sanitized)
            if self._is_already_downloaded(sanitized):
                return

        try:
            info = retry(trigger, label="trigger download")
        except RetryError:
            self.failed_count += 1
            log.error(
                "Could not trigger download. You may not be logged into Nexus Mods, "
                "or this mod is not publicly available."
            )
            return

        download: Download = info.value
        try:
            download.cancel()
        except Exception:  # noqa: BLE001
            pass

        suggested = download.suggested_filename
        suggested_stem, suggested_ext = os.path.splitext(suggested)

        stem = sanitize_filename(preferred_name or suggested_stem)
        self.processed.add(stem)
        if self._is_already_downloaded(stem):
            return

        filename = stem + suggested_ext
        log.info("%d/%d | Fetching: %s", self.current_index, self.total, filename)

        assert self.target_dir is not None
        tmp_path = self.downloads_dir / filename
        try:
            self._stream_to_disk(download.url, tmp_path)
            shutil.move(str(tmp_path), self.target_dir / filename)
        except DownloaderError as exc:
            self.failed_count += 1
            log.error("%s", exc)

    def download_url(self, url: str) -> None:
        if not url or not url.startswith("https://"):
            log.warning("Skipping invalid URL: %r", url)
            return

        def trigger() -> Any:
            with self.page.expect_download() as info:
                try:
                    self.goto(url, retries=False)
                except Exception:  # noqa: BLE001
                    # navigation often errors after a download begins, that's fine
                    pass
            return info

        self._download_from_event(trigger)

    def download_by_id(
        self,
        game: str,
        mod_id: str,
        file_id: str,
        preferred_name: Optional[str] = None,
    ) -> None:
        target_url = (
            f"https://www.nexusmods.com/{game}/mods/{mod_id}?tab=files&file_id={file_id}"
        )

        def trigger() -> Any:
            self.goto(target_url)
            with self.page.expect_download() as info:
                log.debug("Triggering download for %s/%s", mod_id, file_id)
                self.page.evaluate(
                    """
                    () => {
                      const el = document.querySelector('mod-file-download');
                      if (el && el.shadowRoot) {
                        const btn = el.shadowRoot.querySelector('button.nxm-button-secondary-filled-weak');
                        if (btn) btn.click();
                      }
                    }
                    """
                )
            return info

        self._download_from_event(trigger, preferred_name=preferred_name)

    # -- job runner --------------------------------------------------------

    def run_job(self, job: DownloadJob) -> None:
        self.target_dir = job.target_dir
        ensure_dir(job.target_dir)
        self.existing_filenames = {p.stem for p in job.target_dir.iterdir() if p.is_file()}
        self.processed = set()
        self.current_index = 0
        self.total = len(job.items)

        for item in job.items:
            self.current_index += 1
            kind = item["kind"]
            try:
                if kind == "url":
                    self.download_url(item["url"])
                elif kind == "file_id":
                    self.download_by_id(
                        job.game,
                        item["mod_id"],
                        item["file_id"],
                        item.get("name"),
                    )
                elif kind == "mod_category":
                    self._download_category(
                        job.game,
                        item["mod_id"],
                        item["categories"],
                        item["names"],
                    )
                else:
                    log.warning("Unknown item kind: %s", kind)
            except Exception as exc:  # noqa: BLE001
                self.failed_count += 1
                log.exception("Unhandled error processing item %r: %s", item, exc)

        self._cleanup_orphans(job.target_dir)

    def _download_category(
        self,
        game: str,
        mod_id: str,
        categories: Iterable[str],
        names: Iterable[str],
    ) -> None:
        names = list(names)
        if not names:
            return
        for category in categories:
            try:
                data = self.api.list_files(game, mod_id, category)
            except RetryError as exc:
                log.error("Could not list files for mod %s/%s: %s", mod_id, category, exc)
                self.failed_count += 1
                continue
            for wanted in names:
                for entry in data.get("files", []):
                    if wanted == ALL_MARKER or wanted.lower() == entry["name"].lower():
                        stem = os.path.splitext(entry["file_name"])[0]
                        self.download_by_id(game, str(mod_id), str(entry["file_id"]), stem)

    def _cleanup_orphans(self, target_dir: Path) -> None:
        for path in target_dir.iterdir():
            if not path.is_file():
                continue
            if path.stem not in self.processed:
                log.info("Removing orphaned file: %s", path.name)
                try:
                    path.unlink()
                except OSError as exc:
                    log.warning("Could not remove %s: %s", path, exc)

def parse_text_source(path: Path) -> DownloadJob:
    """Parse a .txt file into a :class:`DownloadJob`."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise DownloaderError(f"Empty source file: {path}")

    game = lines[0].strip()
    if not game:
        raise DownloaderError(f"First line of {path} must be the Nexus game domain.")

    items: list[dict] = []
    for raw in lines[1:]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("https://"):
            items.append({"kind": "url", "url": line})
            continue

        # Format: <modId>[:mainName1:mainName2...][;optionalName1;optionalName2...]
        main_part, _, optional_part = line.partition(";")
        first = main_part.split(":")
        mod_id = first[0].strip()
        if not mod_id:
            log.warning("Skipping malformed source line: %r", raw)
            continue

        main_names = [s.strip() for s in first[1:] if s.strip()]
        if not main_names and ":" not in main_part:
            main_names = [ALL_MARKER]

        if optional_part != "" or ";" in line:
            optional_names = [s.strip() for s in optional_part.split(";")]
            optional_names = [s if s else ALL_MARKER for s in optional_names]
        else:
            optional_names = []

        if main_names:
            items.append(
                {
                    "kind": "mod_category",
                    "mod_id": mod_id,
                    "categories": ["main"],
                    "names": main_names,
                }
            )
        if optional_names:
            items.append(
                {
                    "kind": "mod_category",
                    "mod_id": mod_id,
                    "categories": ["optional", "update"],
                    "names": optional_names,
                }
            )

    target_dir = ROOT / sanitize_filename(path.stem)
    return DownloadJob(game=game, target_dir=target_dir, items=items)


def parse_collection_source(page: Page, downloader_goto: Callable[[str], None], url: str) -> DownloadJob:
    """Load a Nexus collection page and convert it to a :class:`DownloadJob`."""
    if not url.endswith("/mods"):
        url = url.rstrip("/") + "/mods"

    match = NEXUS_COLLECTION_RE.match(url) or GAME_DOMAIN_RE.search(url)
    if not match:
        raise DownloaderError(f"Could not extract game domain from URL: {url}")
    game = match.group(1)

    with page.expect_response(
        lambda r: r.request.headers.get("x-graphql-operationname") == "CollectionRevisionMods"
        and r.status == 200,
        timeout=60_000,
    ) as response_info:
        downloader_goto(url)

    response = response_info.value
    payload = response.json()["data"]["collectionRevision"]

    title_selector = (
        ".typography-heading-md.sm\\:typography-heading-lg."
        "text-neutral-strong.break-words.font-semibold"
    )
    try:
        title = page.text_content(title_selector) or f"collection-{int(time.time())}"
    except Exception:  # noqa: BLE001
        title = f"collection-{int(time.time())}"

    items: list[dict] = []
    for resource in payload.get("externalResources", []):
        items.append({"kind": "url", "url": resource["resourceUrl"]})
    for entry in payload.get("modFiles", []):
        file_info = entry["file"]
        mod_info = file_info["mod"]
        name = (
            f"{file_info['name']}-{mod_info['modId']}-{file_info['fileId']}-"
            f"{mod_info['version']}-{file_info['version']}"
        )
        items.append(
            {
                "kind": "file_id",
                "mod_id": str(mod_info["modId"]),
                "file_id": str(entry["fileId"]),
                "name": name,
            }
        )

    target_dir = ROOT / sanitize_filename(title)
    return DownloadJob(game=game, target_dir=target_dir, items=items)


FIREFOX_PROFILE_FILES = (
    "cookies.sqlite",
    "extensions.json",
    "extension-settings.json",
    "extension-preferences.json",
    "extensions",
)


def seed_profile(source: Path, dest: Path) -> None:
    ensure_dir(dest)
    for name in FIREFOX_PROFILE_FILES:
        src = source / name
        dst = dest / name
        if src.is_file():
            shutil.copy2(src, dst)
        elif src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            log.warning("Firefox profile is missing %s (looked in %s)", name, source)


def prevent_sleep(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if enabled else ES_CONTINUOUS
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except OSError:
        pass


def prompt_source() -> str:
    print(
        "Drop or paste a URL to a Nexus collection or a path to a .txt file, then press Enter:"
    )
    return input("> ").strip().strip('"')


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch downloader for Nexus Mods.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to the JSON config file (default: {DEFAULT_CONFIG.name}).",
    )
    parser.add_argument(
        "--source",
        help="Nexus collection URL or path to a .txt source file. "
        "If omitted, you will be prompted interactively.",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Exit instead of prompting for input. Requires --source.",
    )
    return parser.parse_args(argv)


def build_job(
    source: str,
    page: Page,
    downloader_goto: Callable[[str], None],
) -> DownloadJob:
    if NEXUS_COLLECTION_RE.match(source):
        return parse_collection_source(page, downloader_goto, source)
    candidate = Path(source)
    if candidate.is_file() and candidate.suffix.lower() == ".txt":
        return parse_text_source(candidate)
    raise DownloaderError(
        f"Source must be a Nexus collection URL or a path to a .txt file, got: {source!r}"
    )


def run(args: argparse.Namespace) -> int:
    setup_logging()
    log.info("Starting Nexus Mods downloader")

    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    api = NexusAPI(config.apikey)
    if not api.validate():
        log.error("Nexus Mods API key was rejected. Update %s and try again.", args.config)
        return 2

    ensure_dir(DOWNLOADS_DIR)
    ensure_dir(PROFILE_DIR)
    seed_profile(config.firefox_profile, PROFILE_DIR)

    with sync_playwright() as pw:
        context: BrowserContext = pw.firefox.launch_persistent_context(
            str(PROFILE_DIR),
            headless=config.hide,
            accept_downloads=True,
            downloads_path=str(DOWNLOADS_DIR),
            viewport={"width": 1920, "height": 1080},
        )
        try:
            Stealth().apply_stealth_sync(context)
            page = context.pages[0] if context.pages else context.new_page()
            downloader = NexusDownloader(config, api, page, DOWNLOADS_DIR)

            job = _resolve_job(args, page, lambda u: downloader.goto(u))
            if job is None:
                return 1

            log.info("Resolved %d item(s) for game %r -> %s", len(job.items), job.game, job.target_dir)

            prevent_sleep(True)
            try:
                downloader.run_job(job)
            finally:
                prevent_sleep(False)
        finally:
            context.close()

    if downloader.failed_count:
        log.warning(
            "%d operation(s) failed. See %s for details.",
            downloader.failed_count,
            LOG_FILE,
        )
        _open_path(LOG_FILE)

    log.info("Finished. Mods are in %s", job.target_dir)
    if not args.no_prompt:
        input("Press Enter to exit...")
    return 0 if downloader.failed_count == 0 else 1


def _resolve_job(
    args: argparse.Namespace,
    page: Page,
    goto: Callable[[str], None],
) -> Optional[DownloadJob]:
    while True:
        source = args.source or (None if args.no_prompt else prompt_source())
        if not source:
            log.error("No source provided.")
            return None
        try:
            return build_job(source, page, goto)
        except DownloaderError as exc:
            log.error("%s", exc)
            if args.no_prompt or args.source:
                return None
            args.source = None
            continue


def _open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, str(path)], check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Could not open %s: %s", path, exc)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        log.warning("Interrupted by user.")
        return 130
    except Exception as exc:  # noqa: BLE001 - last resort handler
        crash = (
            f"Failure: {exc}\n\n{traceback.format_exc()}"
        )
        try:
            CRASH_FILE.write_text(crash, encoding="utf-8")
            _open_path(CRASH_FILE)
        except OSError:
            sys.stderr.write(crash)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
