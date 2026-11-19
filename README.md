# Nexus Mods Mod Downloader — Wabbajack-aware fork

Downloads mods from a Nexus collection URL or a plain-text mod list, with
resumable writes and exact Wabbajack archive selection.

Based on [`wedsels/NexusModsModDownloader`](https://github.com/wedsels/NexusModsModDownloader).
Upstream has no license file; this fork adds Wabbajack-oriented features and
hardening, and is intended as a contribution back upstream.

## What this fork adds

- `modID|fileID|archiveName` lines fetch the **exact** file a Wabbajack list
  needs. Upstream guesses names through the API and can download the wrong
  file or every file in a mod.
- **Resumable downloads**: bytes go to `<name>.part` first. A `Range` request
  must return `206` (a `200` is refused instead of corrupting the file), the
  stream is `fsync`'d before retry, and the final byte count is checked
  against the Wabbajack manifest before promotion into the cache.
- Already-cached archives are skipped before any network or browser work.
- `NMMD_MODS_DIR` sends finished archives straight into an existing download
  cache.
- Upstream's destructive "delete every unrecognised file in the output
  folder" step is removed. User data is never deleted.

The original collection/mod-ID syntax still works unchanged — see
[`Example_Mod_List.txt`](Example_Mod_List.txt).

## Privacy / secrets

`config.json` in this repo contains **placeholders only**. Never commit a
real API key or your real Firefox profile path. Runtime data is excluded by
`.gitignore`: copied browser profiles (cookies/logins), partial downloads,
logs, generated mod lists and size manifests.

If you accidentally commit a key, revoke it immediately at
<https://next.nexusmods.com/settings/api-keys>.

## Setup

```bat
python -m pip install -r requirements.txt
python -m playwright install firefox
```

1. Install Firefox and sign in to Nexus Mods with a valid account. Close
   Firefox completely before running the downloader (the copied profile must
   be free of lock files).
2. Generate an API key at <https://next.nexusmods.com/settings/api-keys>.
3. Edit `config.json`:

```json
{
  "hide": true,
  "apikey": "YOUR_API_KEY",
  "firefox": "C:/Users/YOU/AppData/Roaming/Mozilla/Firefox/Profiles/PROFILE_NAME"
}
```

4. Run `python "Nexus Mods Mod Downloader.py"` and paste a collection URL or
   the path to a mod list text file.

Non-premium accounts cannot use the API's `download_link` endpoint, so the
browser "Slow download" flow is used. The downloader clicks the real
`Slow download` button inside Nexus's `<mod-file-download>` shadow DOM.

## Wabbajack workflow

Convert the Wabbajack `modlist.txt` (Archives JSON) first:

```bat
python tools\wabbajack_converter.py C:\path\to\Wabbajack\modlist.txt
```

This writes `download_list.txt` (game domain + exact entries) and
`expected_sizes.json` in the repo root. Then either paste
`download_list.txt` into the downloader, or run the optional unattended
runner:

```bat
tools\start_supervisor.bat
```

The supervisor repeats idempotent passes, waits 300 seconds between them
(override with `NMMD_POLL_INTERVAL` or a command-line argument), and stops
when the cache contains every archive. Environment overrides:

| Variable | Meaning |
| --- | --- |
| `NMMD_MODLIST` | path to the generated download list |
| `NMMD_MODS_DIR` | final archive cache (e.g. Wabbajack's `downloads` folder) |
| `NMMD_POLL_INTERVAL` | seconds between passes |

## Optional monitoring / auto-restart (Windows)

- `tools\start_monitor.bat` opens a terminal that streams `master.log` live.
  Closing it never affects downloads.
- `tools\start_watchdog.bat` starts an invisible keeper that restarts the
  supervisor and log mirror if either dies, and reopens the log window.
  Create a `NO_LOG_WINDOW` file in the repo root to stop the window reopening.

These helpers are Windows-only (PowerShell / `pythonw`). The downloader and
converter themselves are cross-platform apart from the Playwright Firefox
setup.

## License

Upstream provides no license. Do not redistribute the original code without
the upstream author's permission.
