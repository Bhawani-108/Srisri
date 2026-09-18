import os
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

DEFAULT_BROKER = "angel_one"
SUPPORTED_BROKERS = ("angel_one", "indmoney", "us_stocks")

def _load_raw_secrets():
    # 1. Direct file load (guaranteed to work across every thread, worker, and script runner)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    toml_path = os.path.join(base_dir, ".streamlit", "secrets.toml")
    
    loaded = {}
    try:
        import tomllib
        if os.path.exists(toml_path):
            with open(toml_path, "rb") as f:
                loaded = tomllib.load(f)
    except Exception:
        try:
            import toml
            if os.path.exists(toml_path):
                with open(toml_path, "r", encoding="utf-8") as f:
                    loaded = toml.load(f)
        except Exception:
            pass

    if loaded:
        return loaded

    # 2. Fallback to st.secrets
    try:
        if hasattr(st, "secrets"):
            return {k: v for k, v in st.secrets.items()}
    except Exception:
        pass

    return {}

def get_active_broker_name() -> str:
    candidate = None
    if get_script_run_ctx() is not None and hasattr(st, "session_state"):
        candidate = st.session_state.get("active_broker")

    if candidate is None:
        candidate = os.getenv("ACTIVE_BROKER", DEFAULT_BROKER)

    broker_name = str(candidate).strip().lower()
    return broker_name if broker_name in SUPPORTED_BROKERS else DEFAULT_BROKER

def get_supported_brokers():
    return list(SUPPORTED_BROKERS)

def get_broker_secrets(broker_name: str | None = None):
    name = (broker_name or get_active_broker_name()).strip().lower()
    secrets = _load_raw_secrets()

    if name == "indmoney":
        cfg = secrets.get("indmoney", {}) if isinstance(secrets.get("indmoney"), dict) else {}
        token = cfg.get("access_token") or secrets.get("INDMONEY_ACCESS_TOKEN") or os.getenv("INDMONEY_ACCESS_TOKEN", "")
        return {
            "access_token": token,
            "base_url": cfg.get("base_url", "https://api.indstocks.com")
        }

    if name == "angel_one":
        cfg = secrets.get("angel_one", {}) if isinstance(secrets.get("angel_one"), dict) else {}
        return {
            "api_key": cfg.get("api_key") or secrets.get("ANGEL_API_KEY") or os.getenv("ANGEL_API_KEY", ""),
            "client_id": cfg.get("client_id") or secrets.get("ANGEL_CLIENT_ID") or os.getenv("ANGEL_CLIENT_ID", ""),
            "password": str(cfg.get("password") or secrets.get("ANGEL_PASSWORD") or os.getenv("ANGEL_PASSWORD", "")),
            "totp_secret": cfg.get("totp_secret") or secrets.get("ANGEL_TOTP_SECRET") or os.getenv("ANGEL_TOTP_SECRET", ""),
        }

    return {}