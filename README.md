# Nexus Mods Mod Downloader

A batch downloader for Nexus Mods that supports both Nexus collection URLs and
plain text manifest files.

## Setup

```sh
pip install -r requirements.txt
python -m playwright install firefox
```

Rename `config.json.example` to `config.json` and fill in:

- `apikey` – your personal Nexus Mods API key (https://www.nexusmods.com/users/myaccount?tab=api)
- `firefox` – path to a Firefox profile that is logged in to Nexus Mods.
  Cookies, extensions and extension settings will be copied from this profile
  into a sandbox under `./profile/` on each run.
- `hide` – `true` to run Firefox headless, `false` to show the window.

## Usage

Interactive mode:

```sh
python nexus_downloader.py
```

Non-interactive:

```sh
python nexus_downloader.py --source "https://www.nexusmods.com/games/<game>/collections/<id>"
python nexus_downloader.py --source ./mylist.txt --no-prompt
```

## Text manifest format

```
<gameDomain>
<modId>[:mainName1:mainName2...][;optionalName1;optionalName2...]
https://www.nexusmods.com/.../some/direct/link
# Comments start with '#' and are ignored.
```

- Use the literal `!All!` (or leave the segment empty after `;`) to grab every
  file in that category.
- The first line must be the Nexus game domain (e.g. `skyrimspecialedition`).

## Output

- Mods are placed in a folder named after the source (collection title or
  manifest filename) next to the script.
- Logs are written to `output.log`. On unhandled crashes a `crash.txt` is
  produced and opened automatically.
