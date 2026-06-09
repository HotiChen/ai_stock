"""
super_trader.py — 老薑操盤手人設 + Gemini 資料補充回饋迴圈

流程：
  1. 每天 8:30 当沖分析時，老薑人設注入 daytrading_analyzer prompt
  2. 分析結果包含 data_quality_score + missing_data
  3. save_daily_feedback() 將今日缺口整理成 Gemini 補充 prompt
  4. 明天早上 morning_briefing.build_briefing_prompt() 讀取並注入
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_FEEDBACK_DIR = Path("data/trader_feedback")


# ═══════════════════════════════════════════════════════════════════════════════
# 人設定義
# ═══════════════════════════════════════════════════════════════════════════════

PERSONA = """你是「老薑」——台灣頂尖當沖操盤手，18 年實戰，前外資券商自營部操盤手。

## 鐵律（違反任一條 → action 必須為 "skip"）
1. 量能不足：volume_ratio < 1.2 倍 → 沒量不做
2. 大盤偏空：台指期貼水 > 0.3% 且大盤下跌 > 0.5% → 空手等待
3. 散戶擁擠：融資水位 > 75% → 轎子已滿，不上車
4. 資料殘缺：data_quality_score < 6 → 資料不足無法判斷，跳過

## 分析順序（依此優先順序給分）
1. 大盤氣氛（台指期 + VIX + 外盤）
2. 量能確認（量比 + 開盤力道）
3. 法人籌碼（外資/投信方向 + 連續天數）
4. 技術型態（突破/VWAP偏離/均線排列）
5. 消息催化（法說、EPS、產業題材）

## 資料品質評分標準
10 = 量能 + 法人 + 融資水位 + 技術 + 消息全齊
8  = 缺融資水位或消息
6  = 缺法人籌碼（只有技術 + 量能）
4  = 技術指標不完整
2  = 僅有代號名稱

## 你的輸出要求
- summary：一句話，像老操盤手說話（直接、有數字、有理由）
- missing_data：條列今日分析時缺少哪些資料（具體說明，供明日 Gemini 補充）
- 停損位不明確時，confidence 自動扣 2 分
"""


def get_persona() -> str:
    return PERSONA


# ═══════════════════════════════════════════════════════════════════════════════
# 昨日 Gemini 補充 hints（注入晨報 prompt）
# ═══════════════════════════════════════════════════════════════════════════════

def get_yesterday_hints() -> str:
    """讀取最近一筆 feedback，回傳格式化的 Gemini 補充請求文字。"""
    for delta in range(1, 5):
        target = date.today() - timedelta(days=delta)
        path = _FEEDBACK_DIR / f"{target}.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("gemini_supplement_prompts", [])
            if not items:
                return ""
            lines = [f"## 老薑昨日補充請求（{target}，今日請優先蒐集）"]
            for item in items[:6]:
                lines.append(f"- {item.get('item','')}: {item.get('prompt','')}")
            return "\n".join(lines)
        except Exception as e:
            log.debug("get_yesterday_hints failed for %s: %s", target, e)
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# 今日回饋儲存
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _AnalysisFeedback:
    code: str
    name: str
    data_quality_score: int
    missing_data: list[str] = field(default_factory=list)


def save_daily_feedback(analyses: list[_AnalysisFeedback]) -> None:
    """
    把今日所有分析的 missing_data 彙整成 Gemini 補充 prompts，
    存至 data/trader_feedback/YYYY-MM-DD.json。
    """
    if not analyses:
        return

    # 彙整所有 missing items（去重）
    seen: set[str] = set()
    supplement_prompts: list[dict] = []

    for a in analyses:
        for item in a.missing_data:
            if item in seen:
                continue
            seen.add(item)
            supplement_prompts.append({
                "item": item,
                "prompt": _item_to_gemini_prompt(item),
            })

    scores = [a.data_quality_score for a in analyses if a.data_quality_score > 0]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    payload = {
        "date": str(date.today()),
        "avg_data_quality": avg_score,
        "analysis_count": len(analyses),
        "gemini_supplement_prompts": supplement_prompts,
    }

    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    path = _FEEDBACK_DIR / f"{date.today()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "老薑回饋儲存完畢 [%s]: 平均品質=%.1f, 補充請求=%d 項",
        date.today(), avg_score or 0, len(supplement_prompts),
    )


def _item_to_gemini_prompt(item: str) -> str:
    """將缺少的資料項目轉成 Gemini 可直接執行的搜尋指令。"""
    templates = {
        "融資水位":   "請查詢 TWSE 最新融資餘額統計表，取各股融資使用率(%)，來源：https://www.twse.com.tw/exchangeReport/MI_MARGN",
        "融資餘額":   "請查詢 TWSE 最新融資餘額統計表，來源：https://www.twse.com.tw/exchangeReport/MI_MARGN",
        "外資買賣超": "請查詢 TWSE 當日外資買賣超前 20 名，來源：https://www.twse.com.tw/fund/T86",
        "投信買賣超": "請查詢 TWSE 當日投信買賣超前 20 名，來源：https://www.twse.com.tw/fund/T86",
        "連續買超":   "請計算外資對該股連續買超天數（最近 5 個交易日）",
        "量比":       "請計算今日成交量 / 近 20 日均量，量比 > 1.5 為強勢放量",
        "VIX":        "請查詢 CBOE VIX 最新收盤價，VIX > 20 為高恐慌，> 30 為極度恐慌",
        "台指期":     "請查詢台指期貨最新報價、溢貼水(%)及未平倉量",
        "ADR":        "請查詢台灣主要 ADR（TSM、ASX、UMC）夜盤漲跌幅",
        "法說":       "請搜尋該股近期法說會日期、EPS 及營收展望",
        "消息":       "請搜尋該股近 3 日重要新聞（法說、合約、評等調整）",
    }
    for key, prompt in templates.items():
        if key in item:
            return prompt
    return f"請補充查詢：{item}（Google Search 或 TWSE 公開資料）"


# ═══════════════════════════════════════════════════════════════════════════════
# 供外部呼叫的簡易介面
# ═══════════════════════════════════════════════════════════════════════════════

def make_feedback(code: str, name: str, score: int, missing: list[str]) -> _AnalysisFeedback:
    return _AnalysisFeedback(code=code, name=name,
                             data_quality_score=score, missing_data=missing)
