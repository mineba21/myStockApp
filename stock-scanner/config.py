import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

SCAN_LOOKBACK_DAYS  = int(os.getenv("SCAN_LOOKBACK_DAYS", "7"))
MA_PERIOD           = int(os.getenv("MA_PERIOD", "150"))
MA_SLOPE_PERIOD     = int(os.getenv("MA_SLOPE_PERIOD", "10"))
VOLUME_SURGE_RATIO  = float(os.getenv("VOLUME_SURGE_RATIO", "1.5"))
VOLUME_AVG_PERIOD   = int(os.getenv("VOLUME_AVG_PERIOD", "20"))

SCHEDULE_TIMES = os.getenv("SCHEDULE_TIMES", "09:00,14:00,22:00").split(",")

KIS_APP_KEY      = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET   = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO   = os.getenv("KIS_ACCOUNT_NO", "")
KIS_ACCOUNT_PROD_CD = os.getenv("KIS_ACCOUNT_PROD_CD", "01")
KIS_IS_PAPER     = os.getenv("KIS_IS_PAPER", "true").lower() == "true"

DATABASE_URL = "sqlite:////Users/mac/.stock_scanner.db"

KR_DATA_PERIOD = "2y"
US_DATA_PERIOD = "2y"
US_UNIVERSE    = os.getenv("US_UNIVERSE", "sp500+nasdaq100")
