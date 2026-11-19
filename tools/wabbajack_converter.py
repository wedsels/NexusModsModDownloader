#!/usr/bin/env python3
"""
Convert a Wabbajack ``modlist.txt`` (Archives JSON) into the downloader's
``modID|fileID|archiveName`` format, plus an optional exact-size sidecar.

    python tools/wabbajack_converter.py path/to/Wabbajack/modlist.txt

Outputs (next to this repo by default, override with positional arguments):
    download_list.txt     first line = Nexus game domain, then one entry per line
    expected_sizes.json   scrubbed archive name -> byte size
    expected_hashes.json  scrubbed archive name -> Wabbajack xxHash64 (base64)

The game domain is auto-detected from the first archive's ``gameName``. Use
``--game DOMAIN`` to override (e.g. ``--game skyrimspecialedition``).
"""

import argparse
import json
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# Wabbajack internal game code -> Nexus API game domain.
GAME_MAP = {
    "skyrimse": "skyrimspecialedition",
    "skyrim": "skyrim",
    "skyrimvr": "skyrimvr",
    "fallout4": "fallout4",
    "fallout4vr": "fallout4vr",
    "falloutnv": "falloutnv",
    "fallout3": "fallout3",
    "oblivion": "oblivion",
}


def scrub(name):
    """Must stay identical to the downloader's filename normalisation."""
    return re.sub(r'[\\/:*?"<>|.]', "", name).strip()


def parse_meta(meta_text):
    game_code, mod_id, file_id = None, None, None
    for line in (meta_text or "").splitlines():
        line = line.strip()
        if line.startswith("gameName="):
            game_code = line.split("=", 1)[1].strip()
        elif line.startswith("modID="):
            try:
                mod_id = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("fileID="):
            try:
                file_id = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    return game_code, mod_id, file_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Wabbajack modlist.txt (Archives JSON)")
    parser.add_argument("output", type=Path, nargs="?", default=BASE / "download_list.txt")
    parser.add_argument("sizes", type=Path, nargs="?", default=BASE / "expected_sizes.json")
    parser.add_argument("hashes", type=Path, nargs="?", default=BASE / "expected_hashes.json")
    parser.add_argument("--game", help="override Nexus game domain")
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    archives = data.get("Archives", [])
    pairs = {}
    sizes = {}
    hashes = {}
    skipped = 0
    detected_game = None

    for archive in archives:
        game_code, mod_id, file_id = parse_meta(archive.get("Meta", ""))
        if mod_id is None or file_id is None:
            skipped += 1
            continue
        detected_game = detected_game or game_code
        name = re.sub(r'[\\/:*?"<>|]', "", archive.get("Name", "")).strip()
        pairs[(mod_id, file_id)] = name
        key = scrub(os.path.splitext(name)[0])
        if key and archive.get("Size"):
            sizes[key] = int(archive["Size"])
        # Wabbajack's Hash is base64(xxHash64(file), little-endian 8 bytes).
        if key and archive.get("Hash"):
            hashes[key] = archive["Hash"]

    game = args.game or GAME_MAP.get((detected_game or "").lower(), detected_game or "skyrimspecialedition")

    args.sizes.write_text(json.dumps(sizes, indent=1, sort_keys=True), encoding="utf-8")
    args.hashes.write_text(json.dumps(hashes, indent=1, sort_keys=True), encoding="utf-8")
    with args.output.open("w", encoding="utf-8", newline="\n") as f:
        f.write(game + "\n")
        for (mod_id, file_id), name in sorted(pairs.items()):
            f.write(f"{mod_id}|{file_id}|{name}\n")

    print("Conversion complete")
    print(f"  Archives in Wabbajack JSON : {len(archives)}")
    print(f"  Non-Nexus entries skipped  : {skipped}")
    print(f"  Download lines written     : {len(pairs)}")
    print(f"  Output                     : {args.output}")
    print(f"  Expected sizes             : {args.sizes} ({len(sizes)} entries)")
    print(f"  Expected Wabbajack hashes  : {args.hashes} ({len(hashes)} entries)")
    print(f"  Game domain                : {game}")


if __name__ == "__main__":
    main()
