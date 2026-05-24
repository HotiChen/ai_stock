from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..schemas.auth import User

router = APIRouter(prefix="/api/scanner", tags=["scanner"])

_MOCK_MARKET_SCAN = {
    "indices": [
        {"key": "TWII", "name": "加權指數", "value": 21845.32, "change": 127.45, "change_pct": 0.0059, "volume_label": "2,482億"},
        {"key": "TPEX", "name": "櫃買指數", "value": 248.73, "change": 1.82, "change_pct": 0.0074, "volume_label": "384億"},
        {"key": "FIMEX", "name": "期貨指數", "value": 21890, "change": 145, "change_pct": 0.0067, "volume_label": "12.4萬口"},
    ],
    "sectors": [
        {"sector": "半導體", "count": 48, "change_pct": 0.0082, "breadth": 0.75, "leaders": ["2330 台積電", "2454 聯發科"]},
        {"sector": "電腦硬體", "count": 35, "change_pct": 0.0054, "breadth": 0.63, "leaders": ["2382 廣達", "2357 華碩"]},
        {"sector": "金融", "count": 42, "change_pct": -0.0012, "breadth": 0.45, "leaders": ["2882 國泰金", "2881 富邦金"]},
        {"sector": "傳產", "count": 56, "change_pct": -0.0023, "breadth": 0.38, "leaders": ["1301 台塑", "1303 南亞"]},
    ],
    "signals": [
        {"code": "2330", "name": "台積電", "signal": "帶量突破季線", "confidence": 0.82},
        {"code": "2382", "name": "廣達", "signal": "突破前高", "confidence": 0.71},
        {"code": "3008", "name": "大立光", "signal": "RSI 黃金交叉", "confidence": 0.68},
        {"code": "2317", "name": "鴻海", "signal": "量比 2.1x 放量", "confidence": 0.65},
    ],
    "news": [
        {
            "source": "經濟日報",
            "time": "09:30",
            "headline": "台積電 CoWoS 先進封裝需求強勁，法人上調目標價至 1200",
            "url": None,
            "related_codes": ["2330"],
        },
        {
            "source": "工商時報",
            "time": "08:45",
            "headline": "AI 晶片需求爆發，聯發科 AI 手機晶片訂單大增",
            "url": None,
            "related_codes": ["2454"],
        },
        {
            "source": "MoneyDJ",
            "time": "10:15",
            "headline": "廣達 AI 伺服器出貨量創新高，Q3 展望樂觀",
            "url": None,
            "related_codes": ["2382"],
        },
    ],
}


@router.get("/")
async def scanner(current_user: User = Depends(get_current_user)) -> dict:
    return _MOCK_MARKET_SCAN
