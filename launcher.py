import sys
import os
import threading
import time
import streamlit.web.cli as stcli
import backend_updater

def run_backend():
    current_portfolio = None
    while True:
        current_portfolio = backend_updater.sync_portfolio_registry(current_portfolio)
        current_portfolio = backend_updater.stream_tick_cycle(current_portfolio)
        time.sleep(3)

def main():
    # Start the backend data updater in a background thread
    backend_thread = threading.Thread(target=run_backend, daemon=True)
    backend_thread.start()
    
    # Start the Streamlit frontend dashboard
    script_path = os.path.join(os.path.dirname(__file__), "frontend_dashboard.py")
    sys.argv = ["streamlit", "run", script_path, "--server.headless=false"]
    
    sys.exit(stcli.main())

if __name__ == "__main__":
    main()