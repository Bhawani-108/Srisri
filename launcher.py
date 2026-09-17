import sys
import os
import streamlit.web.cli as stcli

def main():
    app_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(app_dir)
    script_path = os.path.join(app_dir, "frontend_dashboard.py")
    sys.argv = ["streamlit", "run", script_path, "--server.headless=false"]
    sys.exit(stcli.main())

if __name__ == "__main__":
    main()