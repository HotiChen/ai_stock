from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# H12: 日誌存到專案 logs/ 目錄（非 /tmp），單檔上限 5 MB，保留最近 7 個輪替檔
#
# AI_STOCK_LOG_DIR 可覆寫目的地。這條路徑是以 __file__ 為基準的絕對路徑，
# 光靠 chdir 導不走——測試因此把 68 筆假紀錄（"boom" 之類的 fixture 例外訊息）
# 寫進了正式的 logs/ai_stock.log，其中包含「PostMarketJob 失敗」這種字樣，
# 會讓讀 log 判斷系統健康的 health_check 誤報故障。
# conftest 會把它指向 tmp_path。
_DEFAULT_LOG_DIR = Path(__file__).parent / "logs"
_MAX_BYTES    = 5 * 1024 * 1024   # 5 MB
_BACKUP_COUNT = 7


def _log_dir() -> Path:
    """每次呼叫才解析目的地。

    刻意不在模組載入時求值：測試的 fixture 是在 import 之後才設定
    AI_STOCK_LOG_DIR，模組層級的常數會固定成正式路徑而導不走。
    """
    return Path(os.getenv("AI_STOCK_LOG_DIR") or _DEFAULT_LOG_DIR)


def get_logger(name: str) -> logging.Logger:
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    log_file = log_dir / "ai_stock.log"
    fh = RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
