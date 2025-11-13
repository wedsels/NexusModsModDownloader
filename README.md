### Automatically download mods from either a collection or mod list text file.
- Stop and restart mods mid-download with saved progress
- Rerun script to quickly check for mods missing or needing updates
- Downloads mod at the maximum speed for the user's account level
- Does everything silently in the background
---
1.
- Download [ Python ]( https://www.python.org/downloads/ )
  
2.
- Download [ Firefox ]( https://www.mozilla.org/en-US/firefox/new/ )
- Choose a firefox profile to use by typing about:profiles into the address bar
- Sign in to Nexus Mods with any valid Nexus Mods profile
- [ Generate an API Key at the bottom of this link ]( https://next.nexusmods.com/settings/api-keys )
- Optionally, download a content blocker like UBlock Origin to speed up downloads.
  
3.
- Install the files
    - [ Nexus Mods Mod Downloader.py ]( https://raw.githubusercontent.com/Wedsels/NexusModsModDownloader/refs/heads/main/Nexus%20Mods%20Mod%20Downloader.py )
    - [ config.json ]( https://raw.githubusercontent.com/Wedsels/NexusModsModDownloader/refs/heads/main/config.json )
- Setup config
- Start Nexus Mods Mod Downloader
- Either paste the link to a Nexus Mods collection page, or the path a mod list text file
---
### The syntax for mod list text files is as follows\:
- Game name at the top ( Can be found in the url ex: https://www.nexusmods.com/games/eldenring )
    - eldenring
- Download all the main files for this mod id
    - 88943
- Download only the main file which matches this name
    - 88943:Textures 4k
- Download the matching main files
    - 88943:Textures 4k:Textures Essentials
- Download all the main files and these optional files
    - 88943;Textures LOD;Textures Addon
- Download only this optional file
    - 88943:;Textures Optional Grass
- Download all main files and all optional files
    - 88943;
- Download only all optional files
    - 88943:;
- Download the file at this link ( A link which leads directly to a download )
    - https://I.Am.A.Direct.Download.Link/file/1234/download