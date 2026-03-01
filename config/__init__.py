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
