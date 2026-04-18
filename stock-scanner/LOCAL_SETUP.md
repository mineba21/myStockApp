# Local Setup Guide (desktop/coding)

This guide helps you run this project locally under `~/Desktop/coding`.

## 1) Clone and move into project

```bash
mkdir -p ~/Desktop/coding
cd ~/Desktop/coding
git clone <YOUR_REPO_URL> myStockApp
cd myStockApp/stock-scanner
```

## 2) Create and activate virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3) Environment check

```bash
python -V
python -m pip -V
```

## 4) Run DB initialization

The app auto-initializes DB at runtime, but if needed:

```bash
python -c "from database.models import init_db; init_db(); print('DB initialized')"
```

## 5) Start the web server

```bash
python main.py
```

Then open:
- `http://127.0.0.1:8000`

## 6) Optional: start scheduler (background scans)

```bash
python scheduler.py
```

## 7) Telegram notification check (optional)

Set Telegram values in `config.py` and run a manual scan from UI/API.

---

## macOS quick-start

```bash
cd ~/Desktop/coding/myStockApp/stock-scanner
source .venv/bin/activate
python main.py
```

## Common issues

### `ModuleNotFoundError`
- Ensure `.venv` is activated.
- Re-run `pip install -r requirements.txt`.

### `pykrx` data fetch errors
- Temporary exchange/API/network issue can occur.
- KR ticker filter logic is defensive and falls back to unfiltered universe if market-cap API fails.

### `yfinance` throttling or timeout
- Retry after a short delay.
- Batch download is already used in scanner logic.

---

## Verification commands

```bash
python -m compileall scanner database
python -c "from scanner.market_analysis import get_market_stages; print(get_market_stages(force=True).keys())"
```

If both commands run without errors, your local environment is ready.
