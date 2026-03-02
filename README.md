# Political Edge

Political and regulatory trading intelligence for retail investors. Political Edge tracks political and regulatory events -- Federal Register rules, FDA catalysts, congressional trades, lobbying filings, executive orders, FOMC meetings -- and maps them to market-tradeable signals backed by event study research.

## Architecture

```
collectors (12 modules)
    --> SQLite DB (20 tables, 200K+ records)
        --> analysis pipeline (event studies, signal generation, macro regime)
            --> Streamlit dashboard (11 pages)
```

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url> && cd political-edge

# 2. Create a virtual environment and install dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Copy the example config and add API keys (optional)
cp config/config.example.yaml config/config.yaml

# 4. Initialize the database
python scripts/setup_db.py

# 5. Run data collectors
python scripts/run_collectors.py

# 6. Launch the dashboard
streamlit run dashboard/app.py
```

No API keys are required to start. The app ships with a compressed seed database (`data/seed.db.gz`) containing historical data, which is auto-decompressed on first run. API keys enable live data collection.

## API Keys

All keys are configured in `config/config.yaml` under `api_keys`, or set via environment variables. Both methods work interchangeably.

| Key | Config Field | Environment Variable | Sign Up |
|-----|-------------|---------------------|---------|
| Congress.gov | `congress_gov` | `CONGRESS_GOV_API_KEY` | https://api.congress.gov/sign-up/ |
| Regulations.gov | `regulations_gov` | `REGULATIONS_GOV_API_KEY` | https://open.gsa.gov/api/regulationsgov/ |
| FRED | `fred_api_key` | `FRED_API_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html |
| Alpaca Key ID | `alpaca_key_id` | `APCA_API_KEY_ID` | https://app.alpaca.markets/ |
| Alpaca Secret | `alpaca_secret_key` | `APCA_API_SECRET_KEY` | https://app.alpaca.markets/ |

## Dashboard Pages

| Page | Description |
|------|-------------|
| **Today** | Actionable trading view: active signals, upcoming catalysts, current macro regime |
| **RegWatch** | Regulatory events from the Federal Register, Congress, and Regulations.gov |
| **FDA Catalysts** | Drug approvals, AdCom votes, and PDUFA target action dates |
| **Lobbying** | Lobbying disclosure filings and quarter-over-quarter spending analysis |
| **Watchlist** | Combined view of all data sources for tracked tickers |
| **Macro & Fed** | Hedgeye-style macro regime classifier and FOMC meeting tracker |
| **Signals** | Trading signal generation and paper trade execution |
| **EO Tracker** | Executive order topic classification with evidence-based signals |
| **Settings** | Data collection controls, backfill, backtesting, and data health monitoring |
| **Research** | Five event study research reports with interactive visualizations |
| **Pipeline** | Regulatory pipeline monitor and scenario builder |

## Data Collection

### Manual

```bash
python scripts/run_collectors.py
```

### Automated

A GitHub Actions workflow (`.github/workflows/daily-collect.yml`) runs daily at 6:00 AM ET. It collects fresh data, recompresses the seed database, and commits the updated seed back to the repository.

Required GitHub Actions secrets: `CONGRESS_GOV_API_KEY`, `REGULATIONS_GOV_API_KEY`, `FRED_API_KEY`.

### Seed Database

The file `data/seed.db.gz` ships with historical data. On first run, the dashboard automatically decompresses it to `data/political_edge.db`. When the seed file is updated (detected via file size change), the database is refreshed automatically.

## Research Reports

The platform includes five event study research reports, each with interactive visualizations:

1. **Regulatory Intensity Shocks** -- Agency-level regulatory surges and their impact on sector ETFs
2. **Executive Order Market Impact** -- EO topic classification and corresponding stock reactions
3. **Regulatory Pipeline Rotation** -- Proposed rules generate -0.25% cumulative abnormal return (p=0.016, N=2,000)
4. **Tariff Announcement Asymmetry** -- Sector-level tariff impact dispersion analysis
5. **Macro Regime-Conditional Returns** -- Signal performance variation across economic regimes

## Configuration

The file `config/config.yaml` (copied from `config/config.example.yaml`) controls all runtime behavior.

### Sections

- **`api_keys`** -- API credentials (also settable via environment variables)
- **`schedule`** -- Collection frequency (cron expressions, used by GitHub Actions)
- **`alerts`** -- SMTP email alert configuration (see below)
- **`collection`** -- Rate limits and pagination settings per collector
- **`watchlist`** -- Tracked companies organized by sector, with agency and keyword mappings
- **`contractor_mappings`** -- Company name variants to ticker symbol for USASpending data

### Email Alerts

Five built-in alert rules are preconfigured:

1. High-impact regulatory event (impact score >= 4)
2. New executive order
3. FDA final rule
4. Lobbying spend spike (>25% quarter-over-quarter)
5. Macro regime change

To enable email alerts:

1. Enable Gmail 2-factor authentication and generate an App Password
2. Set `smtp_user`, `smtp_password`, and `email` in the `alerts` section of `config.yaml`

## Deployment

### Streamlit Community Cloud

1. Push the repository to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) and select **New app**
3. Choose the repository, branch, and set the main file path to `dashboard/app.py`
4. Add API keys and alert config in the Streamlit Cloud **Secrets** settings (TOML format, matching the keys in `config/config.example.yaml`)

The `packages.txt` file provides system-level dependencies (`libxml2-dev`, `libxslt-dev`) required by Streamlit Cloud.

## Development

### Project Structure

```
political-edge/
  collectors/          # Data collection modules (12 sources)
  analysis/            # Signal generation, event studies, macro regime
    research/          # Event study research report generators
  execution/           # Paper trading via Alpaca
  dashboard/           # Streamlit app and page definitions
    components/        # Reusable UI components
    pages/             # Individual dashboard pages
  config/              # YAML configuration files
  scripts/             # Database setup, migrations, collection runner
  tests/               # Test suite
  data/                # SQLite database and seed file
  .github/workflows/   # CI (tests) and daily collection automation
```

### Running Tests

```bash
python -m pytest tests/ -v
```

Tests run automatically on push and pull request to `main` via GitHub Actions (`.github/workflows/test.yml`).

### Key Dependencies

- **Streamlit** -- Dashboard framework
- **Plotly** -- Interactive charts and visualizations
- **pandas / scipy / statsmodels** -- Data analysis and event studies
- **yfinance** -- Market data
- **fredapi** -- Federal Reserve economic data
- **alpaca-py** -- Paper trading execution
- **BeautifulSoup / lxml** -- Web scraping for regulatory data
- **PyYAML** -- Configuration management

## License

Private project. No license specified.
