import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

PROJECT_DIR = Path(__file__).parent
OUTPUT_DIR = PROJECT_DIR / "output"
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = DATA_DIR / "jobs.db"

OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

DELAY_MIN = float(os.getenv("DELAY_MIN", "4.0"))
DELAY_MAX = float(os.getenv("DELAY_MAX", "8.0"))
DEFAULT_PAGES = int(os.getenv("DEFAULT_PAGES", "3"))
CAPTCHA_THRESHOLD = 5000
