import sys
import os
import streamlit.web.cli as stcli

def main():
    script_path = os.path.join(os.path.dirname(__file__), "frontend_dashboard.py")
    sys.argv = ["streamlit", "run", script_path, "--server.headless=false"]
    sys.exit(stcli.main())

if __name__ == "__main__":
    main()