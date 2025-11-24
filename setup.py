import os
import subprocess
import json
import urllib.request
import urllib.parse
import sys
import time

# Configuration
MC_VERSION = "1.21" # 1.21.0 is typically just "1.21" in ecosystems
FABRIC_INSTALLER_VERSION = "1.0.1" 
SERVER_DIR = "simulation/server"
MODS_DIR = os.path.join(SERVER_DIR, "mods")

# Modrinth Project IDs
MODS = {
    "fabric-api": "P7dR8mSH",
    "carpet": "TQTTVgYE",
    "worldedit": "1u6JkXh5"
}

def download_file(url, dest):
    print(f"Downloading {url} to {dest}...")
    try:
        # Add a User-Agent to avoid being blocked by some servers
        opener = urllib.request.build_opener()
        opener.addheaders = [('User-Agent', 'ProjectMIRA/1.0')]
        urllib.request.install_opener(opener)
        
        urllib.request.urlretrieve(url, dest)
        print("Done.")
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        # Don't exit immediately, try to continue or let user know
        pass

def get_modrinth_version_url(project_id, game_version):
    # Modrinth API to find version compatible with game_version and fabric loader
    # Parameters need to be properly encoded
    # game_versions=["1.21"] -> game_versions=%5B%221.21%22%5D
    base_url = f"https://api.modrinth.com/v2/project/{project_id}/version"
    
    # Manual encoding to ensure it matches Modrinth's strict array format
    game_version_param = f'["{game_version}"]'
    loaders_param = '["fabric"]'
    
    params = urllib.parse.urlencode({
        'game_versions': game_version_param,
        'loaders': loaders_param
    })
    
    api_url = f"{base_url}?{params}"
    
    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'ProjectMIRA/1.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            if not data:
                print(f"No version found for project {project_id} on {game_version}")
                return None
            
            # Return the primary file of the first (latest) version found
            files = data[0]['files']
            for file in files:
                if file['primary']:
                    return file['url']
            return files[0]['url']
    except Exception as e:
        print(f"Failed to fetch metadata for {project_id}: {e}")
        return None

def setup_server():
    print(f"Setting up Minecraft Server for version {MC_VERSION}...")
    
    if not os.path.exists(SERVER_DIR):
        os.makedirs(SERVER_DIR)
    if not os.path.exists(MODS_DIR):
        os.makedirs(MODS_DIR)

    # 1. Download Fabric Installer
    installer_url = f"https://maven.fabricmc.net/net/fabricmc/fabric-installer/{FABRIC_INSTALLER_VERSION}/fabric-installer-{FABRIC_INSTALLER_VERSION}.jar"
    installer_path = os.path.join(SERVER_DIR, "fabric-installer.jar")
    if not os.path.exists(installer_path):
        download_file(installer_url, installer_path)

    # 2. Install Fabric Server
    # Only install if server.jar doesn't exist or forced
    if not os.path.exists(os.path.join(SERVER_DIR, "fabric-server-launch.jar")):
        print("Installing Fabric Server JAR...")
        try:
            subprocess.run(
                ["java", "-jar", "fabric-installer.jar", "server", "-mcversion", MC_VERSION, "-downloadMinecraft"],
                cwd=SERVER_DIR,
                check=True
            )
            print("Fabric Server installed successfully.")
        except subprocess.CalledProcessError:
            print("Error installing Fabric Server. Ensure Java (17+) is installed and accessible.")
            sys.exit(1)
        except FileNotFoundError:
            print("Java executable not found. Please install Java.")
            sys.exit(1)
    else:
        print("Fabric Server JAR appears to be present. Skipping installation.")

    # 3. Download Mods
    print("Checking mods...")
    for mod_name, project_id in MODS.items():
        # Simple check to see if any jar for this mod exists to avoid re-downloading blindly
        # Ideally we check versions, but for now just check existence of a file starting with name
        existing = [f for f in os.listdir(MODS_DIR) if f.startswith(mod_name)]
        if existing:
            print(f"Mod {mod_name} appears to be present: {existing[0]}")
            continue
            
        url = get_modrinth_version_url(project_id, MC_VERSION)
        if url:
            filename = url.split("/")[-1]
            download_file(url, os.path.join(MODS_DIR, filename))
        else:
            print(f"Skipping {mod_name} (url not found).")

    # 4. Create EULA
    eula_path = os.path.join(SERVER_DIR, "eula.txt")
    if not os.path.exists(eula_path):
        with open(eula_path, "w") as f:
            f.write("eula=true")
            print("EULA accepted.")
    
    # 5. Create server.properties with RCON enabled
    server_props_path = os.path.join(SERVER_DIR, "server.properties")
    if not os.path.exists(server_props_path):
        server_props = """
enable-rcon=true
rcon.port=25575
rcon.password=mira
sync-chunk-writes=false
view-distance=10
difficulty=peaceful
gamemode=creative
enable-command-block=true
"""
        with open(server_props_path, "w") as f:
            f.write(server_props)
        print("server.properties configured.")

    print("\nSetup complete!")
    print(f"To start the server manually: cd {SERVER_DIR} && java -Xmx4G -jar fabric-server-launch.jar nogui")

if __name__ == "__main__":
    setup_server()

