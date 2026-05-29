"""
notion_reporter.py — 每日當沖報告推送到 Notion

在 13:35 PostMarketJob 完成後呼叫。
讀取 daytrading_review.db + adaptive_config.json，
在 Notion「📈 當沖 AI 每日報告」資料庫新增一筆記錄。

環境變數：
  NOTION_TOKEN   : Notion Integration Secret (secret_xxx)
  NOTION_DB_ID   : 目標資料庫 ID（預設已填入）
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── 固定值（建立 DB 時取得）────────────────────────────────────────────────────
_DEFAULT_DB_ID  = os.getenv("NOTION_DB_ID", "1031a6aff6b343ccbed80f24ae9f3b90")
_NOTION_API     = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"

_ADAPTIVE_PATH  = "data/adaptive_config.json"
_REVIEW_DB_PATH = "data/daytrading_review.db"
_LEARNING_PATH  = "data/learning.db"


# ── Notion HTTP ────────────────────────────────────────────────────────────────

def _notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Notion-Version": _NOTION_VERSION,
    }


def _create_page(token: str, db_id: str, props: dict, content: str = "") -> dict:
    """POST /v1/pages — 在資料庫新增一頁。"""
    import requests
    body: dict = {
        "parent": {"database_id": db_id},
        "properties": props,
    }
    if content:
        body["children"] = _markdown_to_blocks(content)

    resp = requests.post(
        f"{_NOTION_API}/pages",
        headers=_notion_headers(token),
        json=body,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _markdown_to_blocks(text: str) -> list:
    """將換行文字轉成 Notion paragraph blocks。"""
    blocks = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("## "):
            blocks.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:]}}]},
            })
        elif line.startswith("### "):
            blocks.append({
                "object": "block", "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:]}}]},
            })
        elif line.startswith("- "):
            blocks.append({
                "object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[2:]}}]},
            })
        else:
            blocks.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": line}}]},
            })
    return blocks


# ── 資料收集 ──────────────────────────────────────────────────────────────────

def _load_today_review(today: str) -> dict:
    """從 daytrading_review.db 讀取今日複盤結果。"""
    result = {
        "total": 0, "hits": 0, "stops": 0, "neutral": 0,
        "best_stock": "", "rows": [],
    }
    try:
        from daytrading_db import DaytradingDB
        db   = DaytradingDB(_REVIEW_DB_PATH)
        rows = db.get_predictions(today)
        for r in rows:
            if r["action"] != "long":
                continue
            result["total"] += 1
            outcome = r["outcome"] or "neutral"
            if outcome == "hit_target":
                result["hits"] += 1
            elif outcome == "hit_stop":
                result["stops"] += 1
            else:
                result["neutral"] += 1

            if not result["best_stock"] or (r["dt_score"] or 0) >= 7:
                name = r["name"] or r["code"]
                result["best_stock"] = f'{r["code"]} {name}（{r["dt_score"]}/10）'

            result["rows"].append(dict(r))

        stats = db.win_rate_summary(days=7)
        result["win_rate_7d"] = round((stats["win_rate"] or 0) * 100, 1)
        stats30 = db.win_rate_summary(days=30)
        result["win_rate_30d"] = round((stats30["win_rate"] or 0) * 100, 1)
    except Exception as e:
        log.warning("_load_today_review failed: %s", e)
    return result


def _load_today_pnl(today: str) -> float:
    """從 learning.db 讀取今日總損益。"""
    try:
        from learning_db import LearningDB
        db  = LearningDB(_LEARNING_PATH)
        log_ = db.get_daily_log(date.fromisoformat(today))
        return log_.total_pnl if log_ else 0.0
    except Exception as e:
        log.warning("_load_today_pnl failed: %s", e)
        return 0.0


def _load_market_change() -> float:
    """取大盤漲跌幅。"""
    try:
        from market_index import fetch_market_index_change
        return round(fetch_market_index_change(), 2)
    except Exception:
        return 0.0


def _load_adaptive() -> dict:
    """讀取 adaptive_config.json，不存在回傳空 dict。"""
    try:
        path = Path(_ADAPTIVE_PATH)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("_load_adaptive failed: %s", e)
    return {}


# ── 屬性組裝 ─────────────────────────────────────────────────────────────────

def _build_properties(today: str, review: dict, pnl: float, market_pct: float, adaptive: dict) -> dict:
    total     = review.get("total", 0)
    hits      = review.get("hits",  0)
    stops     = review.get("stops", 0)
    neutral   = review.get("neutral", 0)
    win_rate  = round(hits / total * 100, 1) if total > 0 else 0.0
    w7d       = review.get("win_rate_7d",  0.0)
    w30d      = review.get("win_rate_30d", 0.0)
    best      = review.get("best_stock", "—")
    version   = adaptive.get("version", 1)
    ai_hint   = adaptive.get("learning_summary", "")[:2000]

    if pnl > 0:
        result_label = "獲利日"
    elif pnl < 0:
        result_label = "虧損日"
    else:
        result_label = "平盤"

    return {
        "日期":     {"title": [{"text": {"content": today}}]},
        "勝率%":    {"number": win_rate},
        "總損益":   {"number": pnl},
        "預測筆數": {"number": total},
        "達目標":   {"number": hits},
        "觸停損":   {"number": stops},
        "未觸發":   {"number": neutral},
        "大盤漲跌%":  {"number": market_pct},
        "近7日勝率%": {"number": w7d},
        "近30日勝率%":{"number": w30d},
        "最高分股票": {"rich_text": [{"text": {"content": best}}]},
        "AI建議調整": {"rich_text": [{"text": {"content": ai_hint}}]},
        "結果":     {"select": {"name": result_label}},
        "學習版本": {"number": version},
    }


def _build_page_content(today: str, review: dict, pnl: float, market_pct: float, adaptive: dict) -> str:
    """組成 Notion 頁面正文（Markdown 格式）。"""
    total = review.get("total", 0)
    hits  = review.get("hits",  0)
    stops = review.get("stops", 0)
    neu   = review.get("neutral", 0)
    rows  = review.get("rows", [])

    win_rate = round(hits / total * 100, 1) if total > 0 else 0.0
    pnl_sign = "+" if pnl >= 0 else ""

    lines = [
        f"## {today} 當沖複盤",
        f"大盤漲跌 {market_pct:+.2f}%　　今日損益 {pnl_sign}{pnl:,.0f} 元",
        "",
        f"## 預測結果（共 {total} 筆）",
        f"- ✅ 達目標：{hits} 筆",
        f"- ❌ 觸停損：{stops} 筆",
        f"- ⬜ 未觸發：{neu} 筆",
        f"- 今日勝率：{win_rate:.1f}%",
        f"- 近 7 日勝率：{review.get('win_rate_7d', 0):.1f}%",
        f"- 近 30 日勝率：{review.get('win_rate_30d', 0):.1f}%",
        "",
        "## 個股明細",
    ]

    for r in rows:
        if r.get("action") != "long":
            continue
        icon    = {"hit_target": "✅", "hit_stop": "❌", "neutral": "⬜"}.get(r.get("outcome", ""), "⬜")
        label   = {"hit_target": "達目標", "hit_stop": "觸停損", "neutral": "未觸發"}.get(r.get("outcome", ""), "未觸發")
        high    = r.get("daily_high")
        low_    = r.get("daily_low")
        close_  = r.get("daily_close")
        tp      = r.get("target_price")
        sl      = r.get("stop_loss")
        summary = r.get("ai_summary", "")[:80]

        price_str = f"高 {high:.1f}　低 {low_:.1f}　收 {close_:.1f}" if high else "無收盤資料"
        lines.append(
            f"{icon} {r['code']} {r['name']}（評分 {r['dt_score']}/10）— {label}"
        )
        lines.append(f"  目標 {tp or '—'}　停損 {sl or '—'}　{price_str}")
        if summary:
            lines.append(f"  AI：{summary}")
        lines.append("")

    # 學習進度
    adaptive_summary = adaptive.get("learning_summary", "")
    if adaptive_summary:
        lines += [
            "## 🧠 AI 學習摘要",
            adaptive_summary,
            "",
        ]

    score_buckets = adaptive.get("score_bucket_winrates", {})
    if score_buckets:
        lines.append("## 各評分區間勝率（累積）")
        for bucket, wr in score_buckets.items():
            bar_len = int(wr * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"- 評分 {bucket}：{bar} {wr*100:.1f}%")

    return "\n".join(lines)


# ── 主入口 ────────────────────────────────────────────────────────────────────

def push_daily_report(
    today: Optional[str] = None,
    db_id: str = _DEFAULT_DB_ID,
) -> bool:
    """
    推送今日報告到 Notion。回傳 True=成功，False=失敗。
    若 NOTION_TOKEN 未設定，記錄 warning 後靜默跳過。
    """
    token = os.getenv("NOTION_TOKEN", "")
    if not token:
        log.warning("NOTION_TOKEN 未設定，跳過 Notion 推送（在 .env 加入 NOTION_TOKEN=secret_xxx）")
        return False

    if today is None:
        today = date.today().isoformat()

    try:
        review     = _load_today_review(today)
        pnl        = _load_today_pnl(today)
        market_pct = _load_market_change()
        adaptive   = _load_adaptive()

        props   = _build_properties(today, review, pnl, market_pct, adaptive)
        content = _build_page_content(today, review, pnl, market_pct, adaptive)

        page = _create_page(token, db_id, props, content)
        log.info("Notion 報告已推送：%s → %s", today, page.get("url", ""))
        return True

    except Exception as e:
        log.error("push_daily_report failed: %s", e)
        return False


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    today_arg = sys.argv[1] if len(sys.argv) > 1 else None
    ok = push_daily_report(today_arg)
    print("✅ 推送成功" if ok else "❌ 推送失敗（見 log）")
