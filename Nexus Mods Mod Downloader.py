all = "!All!"
logs = "./output.log"
crash = "./crash.txt"
profile = "./profile/"
configs = "./config.json"
downloads = "./downloads/"

def clear():
    os.system( "cls" if os.name == "nt" else "clear" )

def fold( d, clean = True ):
    def error( func, path, _ ):
        try:
            os.chmod( path, stat.S_IWRITE )
            func( path )
        except:
            pass

    if clean and os.path.exists( d ):
        shutil.rmtree( d, onexc=error )
    os.makedirs( d, exist_ok=True )

def retry( func, *args, **kwargs ):
    tries = 5
    for attempt in range( tries ):
        try:
            return func( *args, **kwargs )
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/{tries} failed: {e}")
            time.sleep( 1 + attempt )
    logger.error("All retry attempts failed.")
    global failed
    failed = True

    return None

def goto( url, again = True ):
    def go():
        clear()
        logging.info( f"Navigating: { url }" )
        page.goto( url, wait_until="domcontentloaded" )

    if again:
        retry( go )
    else:
        go()

def download( event, filename = None ):
    global failed
    def checkname():
        nonlocal filename
        filename = re.sub( r"[\\/:*?\"<>|.]", "", filename ).strip()

        processedfiles.append( filename )
        if filename in modlist:
            clear()
            logger.info( f"{ modit }/{ ln } | Already Downloaded, Skipping: { filename }" )
            return True
        return False

    if filename and checkname():
        return

    
    ret = retry(event)

    if not ret:
        logger.error(f"Failed to obtain download event for: {filename or 'unknown file'}")
        if filename is not None:
            logger.error(
                "The mod may be inaccessible, or you may not be logged into Nexus Mods."
            )
        return


    file = ret.value
    file.cancel()

    if not filename:
        filename = os.path.splitext( file.suggested_filename )[ 0 ]

    if checkname():
        return

    filename = filename + os.path.splitext( file.suggested_filename )[ 1 ]

    clear()

    logger.info( f"{ modit }/{ ln } | Fetching: { filename }" )

    path = os.path.join( downloads, filename )
    existing_file_size = os.path.getsize( path ) if os.path.exists( path ) else 0

    response = session.get( file.url, allow_redirects=True, stream=True, timeout=30, headers={ "Range": f"bytes={ existing_file_size }-" } if existing_file_size > 0 else {} )

    if response.status_code not in (200, 206):
        logger.error(
            f"HTTP download failed for {filename}. "
            f"Status code: {response.status_code}"
        )
        response.close()
        failed = True
        return

    mb = 1024 * 1024


    resume = existing_file_size > 0 and response.status_code == 206
    start_size = existing_file_size if resume else 0

    with open(path, "ab" if resume else "wb") as f:
        with tqdm.tqdm(
            total=(
                int(response.headers.get("content-length", 0)) + start_size
            ) / mb,
            initial=start_size / mb,
            unit="MB",
            bar_format="{l_bar}{bar}| {n:.1f}/{total:.1f} {unit} {rate_fmt} {remaining}"
        ) as pbar:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
                    pbar.update(len(chunk) / mb)

    response.close()
    shutil.move(path, os.path.join(mods, filename))
    logger.info(f"Successfully downloaded: {filename}")

def url_download( url ):
    if not url or not url.startswith( "https://" ):
        return

    def down():
        with page.expect_download() as info:
            try:
                goto( url, False )
            except:
                pass
            return info
    retry( download, down )

def id_download( id, fileid, filename = None ):
    
    def down():
        goto(
            f"https://www.nexusmods.com/{game}/mods/{id}?tab=files&file_id={fileid}"
        )

        with page.expect_download(timeout=60000) as info:
            page.locator("mod-file-download").evaluate("""
                comp => {
                    const root = comp.shadowRoot;
                    const button = Array.from(
                        root.querySelectorAll('button, a')
                    ).find(el =>
                        (el.innerText || el.textContent || '')
                            .trim().toLowerCase() === 'slow download'
                    );

                    if (!button) {
                        throw new Error('Slow download button not found');
                    }

                    button.click();
                }
            """)

        logger.info("Download event captured successfully")
        return info

    retry(download, down, filename)


def api( id, cat = "", fileid = "" ):
    def request():
        response = session.get( f"https://api.nexusmods.com/v1/games/{ game }/mods/{ id }/files" f"{ '.json?category=' + cat if fileid == '' else '/' + fileid + '.json' }", headers={ "accept": "application/json", "apikey": apikey } )
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception( f"Failed to communicate with nexusmods API for mod: { id }:{ fileid }" )

    return retry( request )

def main():
    global modit, ln

    if path.endswith( ".txt" ):
        for line in data:
            modit += 1
            line = line.strip()
            if line.startswith( "https://" ):
                url_download( line )
                continue

            split = line.split( ";" )
            first = split[ 0 ].split( ":" )

            id = first[ 0 ]
            main = first[ 1: ]

            if not main and ":" not in split[ 0 ]:
                main = [ all ]

            optional = split[ 1: ]
            if optional and optional[ 0 ] == '':
                optional[ 0 ] = all

            def files( type, name ):
                if type:
                    data = api( id, name )
                    if data:
                        for name in type:
                            for entry in data[ "files" ]:
                                if name == all or name.lower() == entry[ "name" ].lower():
                                    id_download( id, entry[ "file_id" ], os.path.splitext( entry[ "file_name" ] )[ 0 ] )

            files( main, "main" )
            files( optional, "optional" )
            files( optional, "update" )
            

    elif path.startswith("https://"):
        external_resources = data.get("externalResources", [])
        required_mods = []
        optional_mods = []

        for item in data["modFiles"]:
            if item.get("optional", False):
                optional_mods.append(item)
            else:
                required_mods.append(item)
        logger.info("Collection information processed successfully.")
        logger.info(
            f"Found {len(required_mods)} required mods, "
            f"{len(optional_mods)} optional mods, "
            f"and {len(external_resources)} external resources."
)
        # Step 1: Handle off-site assets before downloading mods
        if external_resources:
            while True:
                print("\n========== OFF-SITE ASSETS DETECTED ==========")
                print("These resources may require manual downloads.\n")

                for index, resource in enumerate(external_resources, start=1):
                    print(f"{index}. {resource.get('name', 'External resource')}")
                    print(f"   URL: {resource.get('resourceUrl', 'No URL available')}")

                print("\n1. Open external resource links")
                print("2. Skip external assets and continue")
                print("3. Exit downloader")

                choice = input("Choose an option (1-3): ").strip()

                if choice == "1":
                    logger.info("Opening external resource links in the browser.")
                    for resource in external_resources:
                        url = resource.get("resourceUrl")
                        if url:
                            webbrowser.open(url)

                    input(
                        "\nHandle the downloads in your browser, "
                        "then press Enter to return to this prompt..."
                    )

                elif choice == "2":
                    logger.info("User chose to skip external resources.")
                    print("Skipping external assets.")
                    break

                elif choice == "3":
                    print("Exiting the downloader.")
                    logger.info("User exited from the external-resource prompt.")
                    return

                else:
                    print("Invalid option. Please choose 1, 2 or 3.")
        # Step 2: Main collection menu
        while True:
            print("\n========== COLLECTION DOWNLOADER ==========")
            print(f"Required mods: {len(required_mods)}")
            print(f"Optional mods available: {len(optional_mods)}")
            print("\n1. Download all required mods")
            print("2. View and select optional mods")
            print("3. Download ALL optional mods")
            print("4. Skip optional mods")
            print("5. Exit")
            print("============================================")

            choice = input("Choose an option (1-5): ").strip()

            # Option 1: Required mods
            if choice == "1":
                if not required_mods:
                    print("\nNo required mods found.")
                    continue

                ln = len(required_mods)
                modit = 0

                print(f"\nDownloading {len(required_mods)} required mod(s)...")
                logger.info(f"Starting download batch: {len(required_mods)} required mods.")

                for item in required_mods:
                    modit += 1
                    file = item["file"]
                    logger.info(f"Processing required mod: {file['name']}")
                    mod = file["mod"]

                    id_download(
                        mod["modId"],
                        item["fileId"],
                        f"{file['name']}-{mod['modId']}-{item['fileId']}-{mod['version']}-{file['version']}"
                    )

                print("\nRequired mod downloads complete!")

            # Option 2: Select specific optional mods
            elif choice == "2":
                if not optional_mods:
                    print("\nNo optional mods are available.")
                    continue

                print("\nAvailable optional mods:")

                for index, item in enumerate(optional_mods, start=1):
                    file = item["file"]
                    print(f"{index}. {file['name']} (v{file['version']})")

                print("\nSelection guide:")
                print("  1,2,4 - Download mods 1, 2 and 4")
                print("  A     - Download all optional mods")
                print("  S     - Return to the main menu")

                selection = input("\nEnter your selection: ").strip().upper()

                if selection == "S" or not selection:
                    continue

                if selection == "A":
                    selected_mods = optional_mods.copy()
                else:
                    try:
                        indexes = {
                            int(value.strip())
                            for value in selection.split(",")
                        }

                        if any(
                            index < 1 or index > len(optional_mods)
                            for index in indexes
                        ):
                            raise ValueError

                        selected_mods = [
                            optional_mods[index - 1]
                            for index in sorted(indexes)
                        ]

                    except ValueError:
                        print("\nInvalid selection. No mods selected.")
                        continue

                ln = len(selected_mods)
                logger.info(f"User selected {len(selected_mods)} optional mod(s).")
                modit = 0

                print(f"\nDownloading {len(selected_mods)} optional mod(s)...")

                for item in selected_mods:
                    modit += 1
                    file = item["file"]
                    logger.info(f"Processing optional mod: {file['name']}")
                    mod = file["mod"]

                    id_download(
                        mod["modId"],
                        item["fileId"],
                        f"{file['name']}-{mod['modId']}-{item['fileId']}-{mod['version']}-{file['version']}"
                    )

                print("\nSelected optional downloads complete!")

            # Option 3: Download all optional mods
            elif choice == "3":
                if not optional_mods:
                    print("\nNo optional mods are available.")
                    continue

                ln = len(optional_mods)
                modit = 0

                print(f"\nDownloading all {len(optional_mods)} optional mod(s)...")
                logger.info(f"Starting download batch: {len(optional_mods)} optional mods.")
                for item in optional_mods:
                    modit += 1
                    file = item["file"]
                    logger.info(f"Processing optional mod: {file['name']}")
                    mod = file["mod"]

                    id_download(
                        mod["modId"],
                        item["fileId"],
                        f"{file['name']}-{mod['modId']}-{item['fileId']}-{mod['version']}-{file['version']}"
                    )

                print("\nAll optional downloads complete!")

            # Option 4: Exit
            elif choice == "4":
                print("\nSkipping optional mods.")
                print("Collection download workflow complete.")
                return

            elif choice == "5":
                print("\nExiting the collection downloader.")
                return

            else:
                print("\nInvalid option. Please choose 1, 2, 3, 4 or 5.")

# Cleanup disabled for now to prevent accidental deletion of files.
# We can implement safe cleanup once downloaded files are tracked correctly.


def safeimport( package ):
    subprocess.check_call( [ sys.executable, "-m", "pip", "install", "--upgrade", package ] )
    return __import__( package )

if __name__ == "__main__":
    try:
        import os
        import re
        import sys
        import stat
        import time
        import json
        import shutil
        import ctypes
        import logging
        import traceback
        import subprocess
        import webbrowser

        os.system( "mode con: cols=140 lines=3" )

        tqdm = safeimport( "tqdm" )
        requests = safeimport( "requests" )
        playwright = safeimport( "playwright" )
        playwright_stealth = safeimport( "playwright_stealth" )
        subprocess.check_call( [ sys.executable, "-m", "playwright", "install", "firefox" ] )
        from playwright_stealth import Stealth
        from playwright.sync_api import sync_playwright

        clear()

        modit = 0
        failed = False
        session = requests.Session()
        processedfiles = []

        if os.path.exists( logs ):
            os.remove( logs )

        logger = logging.getLogger()
        logger.setLevel( logging.INFO )
        if logger.hasHandlers():
            logger.handlers.clear()
        logger.addHandler( logging.FileHandler( "output.log" ) )
        logger.addHandler( logging.StreamHandler( sys.stdout ) )

        logger.info( "Setting up..." )

        while True:
            with open( configs, "r" ) as file:
                config = json.loads( re.sub( r"\\+", "/", file.read() ) )

                hide = config[ "hide" ]
                if not isinstance( hide, bool ):
                    clear()
                    input( f"Invalid \"hide\" value in { configs }.\nPress any key to continue" )
                    continue

                apikey = config[ "apikey" ]
                if session.get( f"https://api.nexusmods.com/v1/users/validate.json", headers={ "accept": "application/json", "apikey": apikey } ).status_code != 200:
                    clear()
                    input( f"Invalid \"apikey\" value in { configs }.\nPress any key to continue" )
                    continue

                firefox = config[ "firefox" ] + "/"
                if not os.path.isdir( firefox ):
                    clear()
                    input( f"Invalid \"firefox\" value in { configs }.\nPress any key to continue" )
                    continue

                break

        fold( profile, False )
        fold( downloads, False )

        if os.path.isfile( "temp" ):
            os.remove( "temp" )

        def copy( name ):
            if os.path.isfile( firefox + name ):
                shutil.copy( firefox + name, profile + name )
            elif os.path.isdir( firefox + name ):
                shutil.copytree( firefox + name, profile + name, dirs_exist_ok=True )
            else:
                logger.info( f"Error: Could not find { name } at given firefox profile path: { firefox }" )

        copy( "cookies.sqlite" )
        copy( "extensions.json" )
        copy( "extension-settings.json" )
        copy( "extension-preferences.json" )
        copy( "extensions" )

        context = sync_playwright().start().firefox.launch_persistent_context(
            profile,
            headless=hide,
            accept_downloads=True,
            downloads_path=downloads,
            viewport={ "width": 1920, "height": 1080 }
        )
        Stealth().apply_stealth_sync( context )
        page = context.pages[ 0 ] if context.pages else context.new_page()

        while True:
            clear()
            path = input( "Drop or paste a url to a collection or path to a .txt file, then press enter\n" ).strip().strip( '"' )

            if path.startswith( "https://www.nexusmods.com/games/" ) and "/collections/" in path:
                try:
                    if not path.endswith( "/mods" ):
                        path = path + "/mods"
                        
                    logger.info("Waiting for CollectionRevisionMods response...")

                    with page.expect_response( lambda r: r.request.headers.get( "x-graphql-operationname" ) == "CollectionRevisionMods" and r.status == 200, timeout = 30000 ) as response_info:
                        goto( path )
                    logger.info("Collection response captured!")
                    response = response_info.value

                    if response.status == 200:
                            # mods = f"./{ page.text_content( ".typography-heading-md.sm\\:typography-heading-lg.text-neutral-strong.break-words.font-semibold" ) }/"
                            mods = f"./{page.locator('h1').first.inner_text()}/"
                            

                            data = response.json()[ "data" ][ "collectionRevision" ]
                            ln = len( data[ "externalResources" ] ) + len( data[ "modFiles" ] )
                            game = re.search( r'/games/([^/]+)/', path ).group( 1 )
                except Exception:
                    logger.exception(f"Failed to parse collection: {path}")
                    input("Press Enter to continue...")
                    continue
            elif os.path.isfile( path ) and path.endswith( ".txt" ):
                mods = f"./{ os.path.splitext( os.path.basename( path ) )[ 0 ] }/"
                
                with open(path, "r") as file:
                    data = file.readlines()

                if not data or not data[0].strip():
                    logger.error(f"Empty file or missing game name: {path}")
                    continue

                game = data[0].strip()
                data = data[1:]
                ln = len(data)

            else:
                clear()
                logger.info( "Invalid: " + path )
                continue

            if not data:
                clear()
                logger.info( "Invalid: " + path )
                continue

            mods = re.sub( r"[\\/:*?\"<>|.]", "", mods ).strip()

            if not os.path.isdir( mods ):
                os.mkdir( mods )
            modlist = []
            for i in os.listdir( mods ):
                modlist.append( os.path.splitext( i )[ 0 ] )

            break

        ctypes.windll.kernel32.SetThreadExecutionState( 0x80000000 | 0x00000001 )
        main()
        ctypes.windll.kernel32.SetThreadExecutionState( 0x80000000 )
        clear()
        if failed:
            logger.info( f"Some operations failed, details at { logs }" )
            os.startfile( os.path.abspath( logs ) )
        input( f"Finished downloading mods to: { mods }\nDetails at { logs }" )
    except Exception as e:
        with open( crash, "w" ) as f:
            f.write( f"Failure:\n{ str( e ) }\n\n{ traceback.format_exc() }" )
        os.startfile( os.path.abspath( crash ) )