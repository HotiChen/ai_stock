"""Central config loaded from .env — change models here or in .env."""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str) -> str:
    return os.getenv(key, default)


# Ollama
OLLAMA_URL: str = _get("OLLAMA_URL", "http://localhost:11434")
NEWS_MODEL: str = _get("NEWS_MODEL", "qwen2.5:0.5b")
DECISION_MODEL: str = _get("DECISION_MODEL", "qwen2.5:3b")
OLLAMA_TIMEOUT: int = int(_get("OLLAMA_TIMEOUT", "30"))

# Trading
BUDGET: float = float(_get("BUDGET", "30000"))

# Fallback stock names (used when API doesn't return names in simulation mode)
STOCK_NAMES: dict[str, str] = {
    "2330": "台積電",    "2317": "鴻海",      "2454": "聯發科",    "2382": "廣達",
    "3711": "日月光投控","2308": "台達電",    "2303": "聯電",      "2379": "瑞昱",
    "6770": "力積電",    "3034": "聯詠",      "2881": "富邦金",    "2882": "國泰金",
    "2883": "開發金",    "2884": "玉山金",    "2886": "兆豐金",    "2887": "台新金",
    "2891": "中信金",    "2603": "長榮",      "2609": "陽明",      "2615": "萬海",
    "2618": "長榮航",    "2610": "華航",      "5608": "四維航",    "2605": "新興",
    "0050": "元大台灣50","0056": "元大高股息","00631L": "元大台灣50正2",
    "00878": "國泰永續高股息","00919": "群益台灣精選高息","00929": "復華台灣科技優息",
    "2002": "中鋼",      "2006": "東和鋼鐵",  "2008": "高興昌",    "2015": "豐興",
    "9910": "豐泰",      "2007": "燁興",      "2409": "友達",      "3481": "群創",
    "2345": "智邦",      "4966": "譜瑞-KY",   "2344": "華邦電",    "6415": "矽力-KY",
    "3533": "嘉澤",      "5347": "世界",      "2408": "南亞科",    "6669": "緯穎",
    "3231": "緯創",      "3008": "大立光",    "2356": "英業達",    "1590": "亞德客-KY",
    "6285": "啟碁",      "2049": "上銀",      "6515": "穎崴",      "3413": "京鼎",
    "2338": "光罩",      "5285": "界霖",      "2327": "國巨",      "2351": "順德",
    "4736": "友輝",      "3257": "虹冠電",    "6239": "力成",      "3189": "景碩",
    "3706": "神達",      "6533": "訊喬",      "3047": "訊舟",      "2342": "茂矽",
    "8150": "南電",      "6491": "晶宏",      "2426": "鼎元",      "5007": "三液",
    "6269": "台郡",      "3017": "奇鋐",      "2023": "燁輝",      "6547": "高端疫苗",
    "1786": "科妍",      "4174": "浩鼎",      "6509": "聚和國際",  "4126": "太醫",
    "1760": "寶齡富錦",  "6532": "瑞感科技",  "3532": "台勝科",
}
STOP_LOSS_PCT: float = float(_get("STOP_LOSS_PCT", "5"))
# 收盤強制平倉時刻。必須在連續交易時段（09:00–13:24:59）內：
# 13:25–13:30 為收盤集合競價，市價單會被交易所退單。
#
# 單一真相：系統只能有一個「什麼時候平倉」的答案。
# 歷史上有兩個設定（FORCE_CLOSE_TIME 給 ForceCloseJob / 主迴圈，
# DT_FORCE_CLOSE_TIME 給 DaytradingConfig 與模擬），可以各自被 .env 設成不同
# 的值——正式環境確實出現過主迴圈 13:15、當沖設定 13:00 的分歧。
# 現在兩者一律解析成同一個值：DT_FORCE_CLOSE_TIME 若明確設定則以它為準
# （向後相容既有 .env），並在兩者不一致時告警。
def _resolve_force_close_time() -> str:
    import logging

    main_t = os.getenv("FORCE_CLOSE_TIME")
    dt_t = os.getenv("DT_FORCE_CLOSE_TIME")
    if dt_t and main_t and dt_t != main_t:
        logging.getLogger(__name__).warning(
            "FORCE_CLOSE_TIME=%s 與 DT_FORCE_CLOSE_TIME=%s 不一致，"
            "統一採用 %s（系統只能有一個強平時間）", main_t, dt_t, dt_t,
        )
    return dt_t or main_t or "13:15"


FORCE_CLOSE_TIME: str = _resolve_force_close_time()

# Day-trading specific (loaded via daytrading_config.load_daytrading_config())
DT_BUDGET: float           = float(_get("DT_BUDGET", "30000"))
DT_STOP_LOSS_PCT: float    = float(_get("DT_STOP_LOSS_PCT", "3.0"))
DT_TAKE_PROFIT_PCT: float  = float(_get("DT_TAKE_PROFIT_PCT", "9.0"))
DT_TRAILING_START_PCT: float = float(_get("DT_TRAILING_START_PCT", "2.0"))
DT_TRAILING_STOP_PCT: float  = float(_get("DT_TRAILING_STOP_PCT", "0.5"))
#: 與 FORCE_CLOSE_TIME 恆等（見上方 _resolve_force_close_time）。
#: 保留這個名稱是為了不動 daytrading_config 的欄位對應表。
DT_FORCE_CLOSE_TIME: str   = FORCE_CLOSE_TIME
DT_MANUAL_CONFIRM: bool    = _get("DT_MANUAL_CONFIRM", "true").lower() == "true"

# 強平是否放過波段部位（strategy_type == 'swing'）。
#
# 預設 false ＝ 維持既有行為：收盤前把所有部位平掉，不留倉。
# 設為 true ＝ 波段部位留倉過夜——這需要 **T+2 全額交割金**，是資金面的決定，
# 不是技術設定。確定帳戶有足夠交割款再開。
#
# 未標記策略（strategy_type IS NULL）永遠照平：漏平一檔當沖會變成隔日交割
# 義務、可能違約，代價遠高於誤平一檔波段。
PROTECT_SWING_FROM_FORCE_CLOSE: bool = (
    _get("PROTECT_SWING_FROM_FORCE_CLOSE", "false").lower() == "true"
)
