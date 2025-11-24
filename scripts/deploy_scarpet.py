import sys
import os
import shutil

SERVER_ROOT = os.path.abspath("simulation/server")
WORLD_NAME = "world" 
SCRIPTS_DIR = os.path.join(SERVER_ROOT, WORLD_NAME, "scripts")

def deploy_all():
    src_dir = "simulation/scarpet_scripts"
    
    if not os.path.exists(src_dir):
        print(f"Source directory not found: {src_dir}")
        return

    if not os.path.exists(SCRIPTS_DIR):
        os.makedirs(SCRIPTS_DIR, exist_ok=True)

    print(f"Deploying scripts from {src_dir} to {SCRIPTS_DIR}...")
    
    for filename in os.listdir(src_dir):
        if filename.endswith(".sc"):
            src = os.path.join(src_dir, filename)
            dst = os.path.join(SCRIPTS_DIR, filename)
            shutil.copy2(src, dst)
            print(f"  Deployed App {filename}")
        elif filename.endswith(".scl"):
            src = os.path.join(src_dir, filename)
            lib_dir = os.path.join(SCRIPTS_DIR, "lib")
            if not os.path.exists(lib_dir):
                os.makedirs(lib_dir, exist_ok=True)
            dst = os.path.join(lib_dir, filename)
            shutil.copy2(src, dst)
            print(f"  Deployed Lib {filename}")

if __name__ == "__main__":
    deploy_all()

