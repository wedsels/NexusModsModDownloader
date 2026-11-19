#!/usr/bin/env python3
"""
Verify an existing archive cache against the Wabbajack hash manifest.

By default this is read-only; it reports results and records verified files
in ``verified_cache.json`` so later runs only re-hash files whose size or
mtime changed. Use ``--quarantine`` to move bad files aside for redownload.

    python tools/verify_cache.py
    python tools/verify_cache.py --quarantine

Environment:
    NMMD_MODS_DIR   cache to check (default: ./downloads)
"""

import argparse
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import xxhash

BASE = Path(__file__).resolve().parent.parent
SIZES_PATH = BASE / "expected_sizes.json"
HASHES_PATH = BASE / "expected_hashes.json"
MANIFEST_PATH = BASE / "verified_cache.json"
QUARANTINE_DIR = BASE / "quarantine"


def scrub(name):
    return re.sub(r'[\\/:*?"<>|.]', "", name).strip()


def hash_file(path):
    h = xxhash.xxh64()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    digest = h.intdigest().to_bytes(8, "little")
    import base64
    return base64.b64encode(digest).decode("ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path,
                        default=Path(os.environ.get("NMMD_MODS_DIR", BASE / "downloads")))
    parser.add_argument("--quarantine", action="store_true", help="move mismatched files aside")
    args = parser.parse_args()

    sizes = json.loads(SIZES_PATH.read_text(encoding="utf-8"))
    hashes = json.loads(HASHES_PATH.read_text(encoding="utf-8"))
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        manifest = {}

    verified = cached = bad = unknown = hashed = 0
    bytes_hashed = 0
    start = time.time()
    problems = []

    for path in sorted(args.cache.iterdir()):
        if not path.is_file() or path.name.endswith(".part"):
            continue
        key = scrub(os.path.splitext(path.name)[0])
        want_size, want_hash = sizes.get(key), hashes.get(key)
        if want_size is None and want_hash is None:
            unknown += 1
            continue

        stat = path.stat()
        prior = manifest.get(path.name)
        if (prior and prior.get("size") == stat.st_size
                and prior.get("mtime_ns") == stat.st_mtime_ns
                and (not want_hash or prior.get("hash") == want_hash)):
            cached += 1
            continue

        actual_hash = hash_file(path) if want_hash else None
        hashed += 1
        bytes_hashed += stat.st_size
        size_ok = want_size is None or stat.st_size == want_size
        hash_ok = want_hash is None or actual_hash == want_hash

        if size_ok and hash_ok:
            verified += 1
            manifest[path.name] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "hash": actual_hash,
                "checked": datetime.now().isoformat(timespec="seconds"),
            }
        else:
            bad += 1
            detail = f"{path.name}: size={stat.st_size}/{want_size} hash={actual_hash}/{want_hash}"
            problems.append(detail)
            if args.quarantine:
                QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
                target = QUARANTINE_DIR / f"{path.name}.{datetime.now():%Y%m%d-%H%M%S}"
                shutil.move(path, target)
                manifest.pop(path.name, None)

    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)

    print(f"Cache: {args.cache}")
    print(f"  verified now      : {verified}")
    print(f"  trusted from last : {cached}")
    print(f"  mismatched        : {bad}")
    print(f"  unknown to list   : {unknown}")
    print(f"  hashed            : {hashed} files / {bytes_hashed / (1024**3):.2f} GiB in {time.time()-start:.1f}s")
    for p in problems:
        print("  BAD " + p)
    if bad and not args.quarantine:
        print("Re-run with --quarantine to move bad files aside for redownload.")


if __name__ == "__main__":
    main()
