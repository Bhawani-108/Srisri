Directory Tree

algo_trading_app/
│
├── frontend_dashboard.py      # Streamlit app entrypoint, layout & live fragment loop
├── backend_updater.py          # Background Angel One WebSocket & tick cycle engine
├── launcher.py                 # PyInstaller execution entrypoint
├── launcher.spec               # PyInstaller build specification
├── requirements.txt            # Python dependencies
├── styles.css                  # UI styling and dashboard theme
│
├── data/ (or project root)
│   ├── watchlist.txt           # Active ticker list (e.g. SYMBOL:EXCHANGE)
│   ├── portfolio.csv           # Merged live market feed snapshot
│   ├── column_prefs.json       # User column visibility settings
│   ├── manual_buy_dates.csv    # Holdings manual buy date overrides
│   └── mock_portfolio.csv      # Paper trading transactions and open trades
│
├── core/
│   ├── __init__.py
│   ├── storage_manager.py      # Local file IO, caching, and state synchronization
│   ├── symbol_catalog.py       # Scrip mapping, symbol resolution, and F&O parsing
│   └── portfolio_service.py    # Metric calculations (P&L, Valuations, 1D % moves)
│
├── components/
│   ├── __init__.py
│   ├── sidebar.py              # Sidebar search, segment filter pills, ticker add/remove
│   └── mock_editor.py          # Paper trading CSV importer & manual table editor
│
└── views/
    ├── __init__.py
    ├── demat_display.py        # Demat table formatting, ordering, and styling rules
    └── watchlist_display.py    # Watchlist table formatting, ordering, and styling rules

    