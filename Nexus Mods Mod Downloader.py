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
            logger.info( f"{ str( e ) }\nFailed attempt { attempt + 1 }/{ tries }" )
            time.sleep( 1 + attempt )

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
    def checkname():
        processedfiles.append( filename )
        if filename in modlist:
            clear()
            logger.info( f"{ modit }/{ ln } | Already Downloaded, Skipping: { filename }" )
            return True
        return False

    if filename and checkname():
        return

    ret = retry( event )
    if not ret:
        if filename != None:
            logger.info( "You may not be logged into nexus mods\nOr this specific mod is not publicly visible" )
        return

    file = ret.value
    file.cancel()

    if not filename:
        filename = os.path.splitext( file.suggested_filename )[ 0 ]
    
    processedfiles.append( filename )

    if checkname():
        return

    filename = filename + os.path.splitext( file.suggested_filename )[ 1 ]

    clear()

    logger.info( f"{ modit }/{ ln } | Fetching: { filename }" )

    path = os.path.join( downloads, filename )
    existing_file_size = os.path.getsize( path ) if os.path.exists( path ) else 0

    response = session.get( file.url, allow_redirects=True, stream=True, timeout=30, headers={ "Range": f"bytes={ existing_file_size }-" } if existing_file_size > 0 else {} )

    if response.status_code not in ( 200, 206 ):
        logger.info( f"Failed to fetch { filename }\nStatus: { response.status_code }" )
        return

    mb = 1024 * 1024

    with open( path, "ab" if existing_file_size > 0 else "wb" ) as f:
        with tqdm.tqdm(
            total=( int( response.headers.get( "content-length", 0 ) ) + existing_file_size ) / mb,
            initial=existing_file_size / mb,
            unit="MB",
            bar_format="{l_bar}{bar}| {n:.1f}/{total:.1f} {unit} {rate_fmt} {remaining}"
        ) as pbar:
            for chunk in response.iter_content( chunk_size=64 * 1024 ):
                if chunk:
                    f.write( chunk )
                    pbar.update( len( chunk ) / mb )

    shutil.move( path, os.path.join( mods, filename ) )

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
        goto( f"https://www.nexusmods.com/{ game }/mods/{ id }?tab=files&file_id={ fileid }" )

        with page.expect_download() as info:
            page.evaluate(
                """
                const modComponent = document.querySelector( 'mod-file-download' );
                if ( modComponent )
                    modComponent.dispatchEvent( new CustomEvent( 'slowDownload', { bubbles: true, composed: true } ) );
                """
            )

        return info

    retry( download, down, filename )

def api( id, cat = "", fileid = "" ):
    def request():
        response = session.get( f"https://api.nexusmods.com/v1/games/{ game }/mods/{ id }/files" f"{ '.json?category=' + cat if fileid == '' else '/' + fileid + '.json' }", headers={ "accept": "application/json", "apikey": apikey } )
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception( f"Failed to communicate with nexusmods API for mod: { id }:{ fileid }" )

    return retry( request ) 

def main():
    global modit

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
    elif path.startswith( "https://" ):
        for i in data[ "externalResources" ]:
            modit += 1
            url_download( i[ "resourceUrl" ] )
        for i in data[ "modFiles" ]:
            modit += 1
            file = i[ "file" ]
            mod = file[ "mod" ]
            id_download( mod[ "modId" ], i[ "fileId" ], f"{ file[ "name" ] }-{ mod[ "modId" ] }-{ file[ "fileId" ] }-{ mod[ "version" ] }-{ file[ "version" ] }" )
    
    for i in os.listdir( mods ):
        if os.path.splitext( i )[ 0 ] not in processedfiles:
            logger.info( f"File not included, but installed, removing: { i }" )
            if os.path.exists( mods + i ):
                os.remove( mods + i )

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

        os.system( "mode con: cols=140 lines=3" )

        tqdm = safeimport( "tqdm" )
        requests = safeimport( "requests" )
        playwright = safeimport( "playwright" )
        playwright_stealth = safeimport( "playwright_stealth" )
        subprocess.check_call( "playwright install firefox", shell=True )
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
                config = json.load( file )

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

                    with page.expect_response( lambda r: r.request.headers.get( "x-graphql-operationname" ) == "CollectionRevisionMods" and r.status == 200 ) as response_info:
                        goto( path )

                    response = response_info.value

                    if response.status == 200:
                            mods = f"./{ page.text_content( ".typography-heading-md.sm\\:typography-heading-lg.text-neutral-strong.break-words.font-semibold" ) }/"
                            data = response.json()[ "data" ][ "collectionRevision" ]
                            ln = len( data[ "externalResources" ] ) + len( data[ "modFiles" ] )
                            game = re.search( r'/games/([^/]+)/', path ).group( 1 )
                except Exception as e:
                    clear()
                    logger.info( f"{ str( e ) }\nFailed to parse collection: { path }" )
                    continue
            elif os.path.isfile( path ) and path.endswith( ".txt" ):
                mods = f"./{ os.path.splitext( os.path.basename( path ) )[ 0 ] }/"
                with open( path, "r" ) as file:
                    data = file.readlines()
                    game = data[ 0 ].strip()
                    data = data[ 1: ]
                    ln = len( data )
            else:
                clear()
                logger.info( "Invalid: " + path )
                continue

            if not data:
                clear()
                logger.info( "Invalid: " + path )
                continue

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