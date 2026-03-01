import os
import yaml

_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_CONFIG_DIR)

DB_PATH = os.path.join(_PROJECT_ROOT, "data", "political_edge.db")


def load_config() -> dict:
    path = os.path.join(_CONFIG_DIR, "config.yaml")
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)

    # Allow env var overrides for API keys
    env_map = {
        "congress_gov": "CONGRESS_GOV_API_KEY",
        "regulations_gov": "REGULATIONS_GOV_API_KEY",
        "quiver_quant": "QUIVER_QUANT_API_KEY",
        "tiingo": "TIINGO_API_KEY",
        "fred_api_key": "FRED_API_KEY",
        "alpaca_key_id": "APCA_API_KEY_ID",
        "alpaca_secret_key": "APCA_API_SECRET_KEY",
    }
    for key, env_var in env_map.items():
        env_val = os.environ.get(env_var)
        if env_val:
            cfg["api_keys"][key] = env_val

    return cfg


def load_sector_mappings() -> dict[str, list[str]]:
    path = os.path.join(_CONFIG_DIR, "sector_mappings.yaml")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_pharma_companies() -> list[dict]:
    """Load pharma company name → ticker mappings."""
    path = os.path.join(_CONFIG_DIR, "pharma_companies.yaml")
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("pharma_companies", [])


def load_tariff_events() -> list[dict]:
    """Load hardcoded tariff event dates."""
    path = os.path.join(_CONFIG_DIR, "tariff_events.yaml")
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("tariff_events", [])


def load_fomc_dates() -> list[dict]:
    """Load FOMC meeting dates."""
    path = os.path.join(_CONFIG_DIR, "fomc_dates.yaml")
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("fomc_meetings", [])


def get_api_key(key_name: str) -> str | None:
    """Get an API key from config or env. Returns None if not configured."""
    cfg = load_config()
    val = cfg.get("api_keys", {}).get(key_name, "")
    return val if val else None
