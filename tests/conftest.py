import os
import sys
from pathlib import Path

os.environ["SCRAPE_DELAY_MAX"] = "0"   # テストでは書店サイトへのランダム待機を無効に
os.environ["SCRAPE_DELAY_MIN"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
