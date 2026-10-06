import subprocess
import sys
import os

from config import BOT_VERSION

def main():
    print(f"[INFO] Initializing AlphaScalp Streamlit Dashboard ({BOT_VERSION})...")
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.py")
    
    # Run streamlit process
    try:
        subprocess.run([sys.executable, "-m", "streamlit", "run", dashboard_path])
    except KeyboardInterrupt:
        print("\n[INFO] Shutdown signal received. Closing dashboard process.")
        sys.exit(0)

if __name__ == "__main__":
    main()
