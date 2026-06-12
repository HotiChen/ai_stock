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


# ═══════════════════════════════════════════════════════════════════════════════
# 收盤後老薑檢討
# ═══════════════════════════════════════════════════════════════════════════════

def run_postmarket_review(
    db_path: str = "data/daytrading_review.db",
    today: Optional[str] = None,
) -> str:
    """
    收盤後讓老薑檢討今日預測結果，找出「哪筆錯了、缺什麼資料」，
    生成 Gemini 明日補充請求，存入 data/trader_feedback/。

    回傳 Telegram HTML 摘要（可直接發送）。
    """
    import re as _re
    from daytrading_db import DaytradingDB
    from ai_client import call_sonnet

    if today is None:
        today = date.today().isoformat()

    # 讀取今日已複盤的預測結果
    db   = DaytradingDB(db_path)
    rows = db.get_predictions(today)
    reviewed = [r for r in rows if r["outcome"] is not None]

    if not reviewed:
        log.info("run_postmarket_review: 今日無已複盤記錄，跳過")
        return ""

    # 已執行過（ai_commentary 都填了）→ 不重複跑
    if all(r["ai_commentary"] for r in reviewed):
        log.info("run_postmarket_review: 今日已完成，跳過")
        return ""

    # ── 組成老薑檢討 prompt ───────────────────────────────────────────────────
    stats = db.win_rate_summary(days=30)
    win_rate_today = (
        sum(1 for r in reviewed if r["was_correct"] == 1) / len(reviewed)
        if reviewed else 0
    )

    cases = []
    for r in reviewed:
        outcome_zh = {"hit_target": "✅達目標", "hit_stop": "❌觸停損", "neutral": "⬜未觸發"}
        cases.append(
            f"  {r['code']} {r['name']} (評分{r['dt_score']}/10)\n"
            f"  預測: {r['action']} 進場{r['entry_low']}–{r['entry_high']} "
            f"目標{r['target_price']} 停損{r['stop_loss']}\n"
            f"  AI當時判斷: {r['ai_summary']}\n"
            f"  實際: {outcome_zh.get(r['outcome'], r['outcome'])} "
            f"最高{r['daily_high']} 最低{r['daily_low']} 收{r['daily_close']}"
        )

    prompt = f"""你是「老薑」，今天 ({today}) 做了 {len(reviewed)} 筆當沖預測，結果如下：

{chr(10).join(cases)}

今日勝率：{win_rate_today:.0%}
近 30 日勝率：{stats['win_rate']*100:.1f}% ({stats['total']} 筆)

## 你的任務：收盤後誠實檢討

### Part 1 — 逐筆分析
對每筆預測（尤其是 hit_stop 和 neutral）：
- what_went_wrong: 老實說這筆為什麼錯了或沒進場（一句話，要有具體原因）
- missing_data: 如果當時有哪些資料，你的判斷會不同？（具體說）
- lesson: 這筆學到什麼

### Part 2 — 給 Gemini 的補充請求
根據今日所有缺失，列出明天晨報 Gemini 應優先補充的資料（按重要性排序）：
- 每項要具體（不要說「更多資料」，要說「台積電融資使用率%」）
- 附上 Gemini 可直接執行的查詢指令

### Part 3 — 整體評估
- 今日勝率合理嗎？哪個環節最需要改善？
- 一句話給明天的自己

只回傳 JSON：
{{
  "reviews": [
    {{
      "code": "代號",
      "outcome": "hit_target|hit_stop|neutral",
      "what_went_wrong": "具體原因或null",
      "missing_data": ["缺少項目"],
      "lesson": "這筆學到什麼"
    }}
  ],
  "gemini_priorities": [
    {{
      "item": "具體資料名稱",
      "importance": "高|中|低",
      "prompt": "Gemini 可直接執行的查詢指令"
    }}
  ],
  "win_rate_today": {win_rate_today:.2f},
  "overall_assessment": "整體評估一句話",
  "note_to_tomorrow_self": "給明天自己的話"
}}"""

    log.info("run_postmarket_review: 呼叫老薑檢討 %d 筆...", len(reviewed))
    raw = call_sonnet(prompt, max_tokens=4096)
    if not raw:
        log.warning("run_postmarket_review: Claude 回應為空")
        return ""

    # 解析 JSON
    result: dict = {}
    try:
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        result = json.loads(m.group()) if m else {}
    except Exception as e:
        log.warning("run_postmarket_review: JSON 解析失敗: %s", e)
        result = {"raw": raw}

    result["date"]         = today
    result["generated_at"] = datetime.now().isoformat()

    # ── 回填 ai_commentary 到 DB ─────────────────────────────────────────────
    for rev in result.get("reviews", []):
        code = rev.get("code", "")
        commentary = f"{rev.get('what_went_wrong','') or ''} | {rev.get('lesson','') or ''}".strip(" |")
        if commentary and code:
            try:
                # Re-use save_review with just commentary update (find matching row)
                with __import__("sqlite3").connect(db_path) as conn:
                    conn.execute(
                        "UPDATE dt_prediction_log SET ai_commentary=? WHERE date=? AND code=?",
                        (commentary, today, code),
                    )
            except Exception as e:
                log.debug("update ai_commentary failed for %s: %s", code, e)

    # ── 存回饋檔（合併或建立）───────────────────────────────────────────────
    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    fb_path = _FEEDBACK_DIR / f"{today}.json"

    existing: dict = {}
    if fb_path.exists():
        try:
            existing = json.loads(fb_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 合併：用 postmarket 的 gemini_priorities 蓋掉或補充 morning 的
    existing["postmarket_review"]       = result
    existing["gemini_supplement_prompts"] = [
        {"item": p["item"], "prompt": p["prompt"]}
        for p in result.get("gemini_priorities", [])
    ]
    existing["date"]         = today
    existing["generated_at"] = datetime.now().isoformat()

    fb_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "run_postmarket_review: 回饋已儲存 [%s] win_rate=%.0f%% gemini_items=%d",
        today, win_rate_today * 100,
        len(result.get("gemini_priorities", [])),
    )

    # ── Telegram 摘要 ────────────────────────────────────────────────────────
    wins     = sum(1 for r in reviewed if r["was_correct"] == 1)
    losses   = sum(1 for r in reviewed if r["was_correct"] == 0)
    note     = result.get("note_to_tomorrow_self", "")
    assess   = result.get("overall_assessment", "")
    lines = [
        f"🧓 <b>老薑收盤檢討 [{today}]</b>",
        f"今日勝率 {wins}/{len(reviewed)} ({win_rate_today:.0%})　近30日 {stats['win_rate']*100:.1f}%",
        "",
    ]

    for rev in result.get("reviews", []):
        icon = {"hit_target": "✅", "hit_stop": "❌", "neutral": "⬜"}.get(rev.get("outcome",""), "•")
        lines.append(f"{icon} <b>{rev.get('code')}</b>: {rev.get('what_went_wrong') or '—'}")
        for m_item in rev.get("missing_data", [])[:2]:
            lines.append(f"   缺: {m_item}")

    if result.get("gemini_priorities"):
        lines.append("")
        lines.append("📝 <b>明日 Gemini 補充</b>")
        for p in result["gemini_priorities"][:4]:
            imp = {"高": "🔴", "中": "🟡", "低": "⚪"}.get(p.get("importance",""), "•")
            lines.append(f"  {imp} {p.get('item')}")

    if assess:
        lines.append(f"\n<i>{assess}</i>")
    if note:
        lines.append(f"<i>給明天的自己：{note}</i>")

    return "\n".join(lines)
