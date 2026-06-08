#!/usr/bin/env python3
"""
dev_tools.py — QUANT·AI 開發 / 測試工具

用法（在 ai_stock/ 目錄下執行）：

  python dev_tools.py shioaji                  # 測試 Shioaji 連線 + 即時報價
  python dev_tools.py telegram                 # 發送 Telegram 測試訊息
  python dev_tools.py morning [YYYY-MM-DD]     # 手動執行 8:30 晨報選股流程
  python dev_tools.py daytrade                 # 手動執行 8:30 當沖預測報告
  python dev_tools.py confirm                  # 手動執行 9:05 開盤確認
  python dev_tools.py review [YYYY-MM-DD]      # 資深交易員 AI 審查 + Gemini 改善建議
  python dev_tools.py db [YYYY-MM-DD]          # 查看 DB 當日狀態
  python dev_tools.py full [YYYY-MM-DD]        # morning → review → telegram（完整流程）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dev_tools")

_DB_PATH       = os.getenv("DB_PATH", "data/research.db")
_CHAT_ID       = os.getenv("TELEGRAM_CHAT_ID", "")
_BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
_API_KEY       = os.getenv("SHIOAJI_API_KEY", "")
_SECRET_KEY    = os.getenv("SHIOAJI_SECRET_KEY", "")
_SIMULATION    = os.getenv("SHIOAJI_SIMULATION", "true").lower() != "false"
_PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() != "false"
_BUDGET        = float(os.getenv("BUDGET", "1000000"))
_FEEDBACK_DIR  = Path("data/trader_feedback")


# ═══════════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _sep(title: str = "") -> None:
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'═' * pad} {title} {'═' * pad}")
    else:
        print("═" * width)


def _parse_date(s: str | None) -> date:
    if s:
        return date.fromisoformat(s)
    return date.today()


def _connect_shioaji():
    """Return (api, error_str). api is None on failure."""
    if not _API_KEY or not _SECRET_KEY:
        return None, "未設定 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY"
    try:
        import shioaji as sj
        api = sj.Shioaji(simulation=_SIMULATION)
        api.login(api_key=_API_KEY, secret_key=_SECRET_KEY, fetch_contract=True)
        return api, None
    except Exception as e:
        return None, str(e)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. shioaji
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_shioaji() -> None:
    _sep("Shioaji 連線測試")
    print(f"  SHIOAJI_SIMULATION = {_SIMULATION}")
    print(f"  API Key            = {_API_KEY[:8]}..." if _API_KEY else "  API Key  = ❌ 未設定")

    api, err = _connect_shioaji()
    if err:
        print(f"\n  ❌ 連線失敗: {err}")
        if "ca_required" in err.lower() or "certificate" in err.lower():
            print("     → 真實模式需要 CA 憑證，請設定 SHIOAJI_SIMULATION=true")
        return

    print(f"\n  ✅ 登入成功 (simulation={_SIMULATION})")

    # Sample prices
    test_codes = ["2330", "2317", "2454", "2412", "3008", "2603", "6669"]
    print("\n  📊 樣本報價：")
    for code in test_codes:
        try:
            contract = api.Contracts.Stocks.get(code)
            if contract:
                snaps = api.snapshots([contract])
                if snaps:
                    s = snaps[0]
                    close  = getattr(s, "close", "?")
                    chg    = getattr(s, "change_price", 0)
                    volume = getattr(s, "volume", "?")
                    print(f"     {code}  {close:>8}  {chg:+.2f}  量={volume:,}")
                else:
                    print(f"     {code}  (無快照資料)")
            else:
                print(f"     {code}  (合約不存在)")
        except Exception as e:
            print(f"     {code}  ❌ {e}")

    # Account info
    try:
        accs = api.list_accounts()
        print(f"\n  💰 帳戶數: {len(accs or [])}")
    except Exception as e:
        print(f"\n  帳戶資訊取得失敗: {e}")

    try:
        api.logout()
        print("\n  ✅ 已登出")
    except Exception:
        pass

    if _SIMULATION:
        print("\n  ⚠️  模擬模式：報價為 Shioaji 模擬資料（非真實市價）")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. telegram
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_telegram(custom_msg: str | None = None) -> None:
    _sep("Telegram 測試")
    if not _BOT_TOKEN or not _CHAT_ID:
        print("  ❌ 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        return

    print(f"  Chat ID = {_CHAT_ID}")

    try:
        from telegram_bot import send_text
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = custom_msg or (
            f"🔧 <b>QUANT·AI 測試訊息</b>\n"
            f"時間: {now}\n"
            f"SHIOAJI_SIMULATION: {_SIMULATION}\n"
            f"PAPER_TRADING: {_PAPER_TRADING}\n"
            f"BUDGET: {int(_BUDGET):,}\n"
            f"\n✅ Telegram 連線正常"
        )
        send_text(_CHAT_ID, text)
        print("  ✅ 訊息已送出")
    except Exception as e:
        print(f"  ❌ 發送失敗: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. morning — PremarketJob pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_morning(target_date: date | None = None, send_telegram: bool = False) -> list[dict]:
    """手動執行 8:30 晨報選股（掃候選股 → AI 深度分析 → 風控 → 寫 DB）。"""
    if target_date is None:
        target_date = date.today()
    _sep(f"晨報選股  [{target_date}]")

    from research_db import init_db
    from main import scan_candidates, PremarketJob
    init_db(_DB_PATH)

    # Shioaji
    print("  ⏳ 連接 Shioaji...")
    api, err = _connect_shioaji()
    if err:
        print(f"  ⚠️  Shioaji 離線: {err} → 使用 TWSE fallback")
    else:
        print(f"  ✅ Shioaji 已連線 (simulation={_SIMULATION})")

    # Scan
    print("\n  📊 Step 1: 掃描候選股...")
    candidates = scan_candidates(api)
    print(f"      → {len(candidates)} 支候選股")
    if candidates:
        for c in candidates[:5]:
            print(f"         {c.get('code')} {c.get('name',''):<6} price={c.get('price','?')}")
        if len(candidates) > 5:
            print(f"         ... (還有 {len(candidates)-5} 支)")

    # Deep analysis + risk guard
    print("\n  🤖 Step 2: AI 深度分析 + 風控審查...")
    print("     (可能需要幾分鐘，依候選股數量而定)")

    job = PremarketJob(
        candidates=candidates,
        capital=_BUDGET,
        db_path=_DB_PATH,
        telegram_chat_id=_CHAT_ID if send_telegram else None,
        current_positions=None,
        api=api,
    )
    approved = job.run()

    print(f"\n  ✅ 通過: {len(approved)} 檔 → 已寫入 DB ({_DB_PATH})")
    for p in approved:
        print(f"     {p['code']} {p.get('name',''):<6} "
              f"conf={p.get('confidence',0):.2f}  "
              f"target={p.get('target_price',0):.0f}  "
              f"stop={p.get('stop_loss_price',0):.0f}")

    if not approved:
        print("     (無通過審核的候選股)")

    if api:
        try:
            api.logout()
        except Exception:
            pass

    return approved


# ═══════════════════════════════════════════════════════════════════════════════
# 4. daytrade — daytrading report
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_daytrade(send_telegram: bool = False) -> str:
    """手動執行 8:30 當沖預測報告。"""
    _sep("當沖預測報告")
    print("  ⏳ 連接 Shioaji...")
    api, err = _connect_shioaji()
    if err:
        print(f"  ⚠️  Shioaji 離線: {err} → 使用 TWSE fallback")
    else:
        print(f"  ✅ Shioaji 已連線")

    print("\n  📊 分析中（掃描全市場，約需 1–3 分鐘）...")
    try:
        from daytrading_report import build_daytrading_report
        report = build_daytrading_report(api=api)
        print("\n  ─── 報告內容 ───")
        # Strip HTML tags for terminal
        clean = re.sub(r"<[^>]+>", "", report)
        print(clean)
        print("  ───────────────")

        if send_telegram and _CHAT_ID:
            from telegram_bot import send_text
            send_text(_CHAT_ID, report)
            print("\n  ✅ 已發送到 Telegram")

        # Save positions for 9:05 confirm
        print("\n  💾 儲存當沖預測部位...")
        try:
            from daytrading_monitor import load_daytrading_positions
            positions = load_daytrading_positions()
            print(f"     → {len(positions)} 支部位 (watching={sum(1 for p in positions if p.status=='watching')})")
        except Exception as e:
            print(f"     ⚠️  讀取部位失敗: {e}")

        return report
    except Exception as e:
        print(f"  ❌ 失敗: {e}")
        return ""
    finally:
        if api:
            try:
                api.logout()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# 5. confirm — 9:05 opening confirm
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_confirm(send_telegram: bool = False) -> None:
    """手動執行 9:05 開盤確認（AI 重新確認每支當沖是否進場）。"""
    _sep("9:05 開盤確認")
    print("  ⏳ 連接 Shioaji...")
    api, err = _connect_shioaji()
    if err:
        print(f"  ⚠️  Shioaji 離線: {err}")
    else:
        print(f"  ✅ Shioaji 已連線")

    # Shioaji 連線狀態訊息
    if send_telegram and _CHAT_ID:
        try:
            from telegram_bot import send_text
            sj_mode = "模擬" if _SIMULATION else "真實"
            sj_status = "✅ 已連線" if api else "❌ 未連線"
            send_text(_CHAT_ID,
                f"🔌 <b>[dev] Shioaji 連線狀態</b>\n"
                f"{sj_status}（{sj_mode}模式）\n"
                f"SHIOAJI_SIMULATION={_SIMULATION}\n"
                f"PAPER_TRADING={_PAPER_TRADING}")
        except Exception as e:
            print(f"  ⚠️  Telegram 通知失敗: {e}")

    try:
        from daytrading_config import load_daytrading_config
        from main import _opening_confirm_dt_positions
        dt_config = load_daytrading_config()

        # Temporarily patch TELEGRAM_CHAT_ID in main module
        import main as _main
        orig_chat = getattr(_main, "TELEGRAM_CHAT_ID", "")
        _main.TELEGRAM_CHAT_ID = _CHAT_ID if send_telegram else ""
        try:
            _opening_confirm_dt_positions(api, dt_config)
        finally:
            _main.TELEGRAM_CHAT_ID = orig_chat

        print("  ✅ 開盤確認完成")
    except Exception as e:
        print(f"  ❌ 失敗: {e}")
    finally:
        if api:
            try:
                api.logout()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# 6. review — Senior trader AI review + Gemini feedback
# ═══════════════════════════════════════════════════════════════════════════════

def _load_recent_feedback(days: int = 5) -> str:
    """Load recent feedback summaries to give Claude context on history."""
    if not _FEEDBACK_DIR.exists():
        return ""
    files = sorted(_FEEDBACK_DIR.glob("*.json"), reverse=True)[:days]
    if not files:
        return ""
    parts = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            score = d.get("overall_score", "?")
            missing = ", ".join(d.get("gemini_improvement", {}).get("add_data_sources", [])[:2])
            parts.append(f"  [{f.stem}] 評分={score}/10  改善需求={missing or '無'}")
        except Exception:
            pass
    return "\n".join(parts) if parts else ""


def cmd_review(target_date: date | None = None, send_telegram: bool = False) -> dict:
    """資深交易員 AI 審查今日選股品質，輸出 Gemini 資料改善建議。"""
    if target_date is None:
        target_date = date.today()
    _sep(f"資深交易員審查  [{target_date}]")

    # Load picks from DB
    from research_db import init_db, load_daily_plan
    init_db(_DB_PATH)
    picks = load_daily_plan(target_date, _DB_PATH)

    # Load morning briefing context
    briefing_ctx = ""
    bp = Path(f"data/morning_briefings/{target_date}.json")
    if bp.exists():
        try:
            bd = json.loads(bp.read_text(encoding="utf-8"))
            briefing_ctx = (
                f"市場展望: {bd.get('market_outlook','N/A')}\n"
                f"主要風險: {', '.join(bd.get('key_risks',[])[:3])}\n"
                f"主要機會: {', '.join(bd.get('key_opportunities',[])[:3])}"
            )
        except Exception:
            pass

    if not picks and not briefing_ctx:
        print("  ⚠️  無選股資料。請先執行: python dev_tools.py morning")
        return {}

    prev_feedback = _load_recent_feedback()
    picks_json = json.dumps(picks or [], ensure_ascii=False, indent=2)

    prompt = f"""你是一位有 20 年經驗的台股資深交易員，專精日內當沖與量化策略。
今天是 {target_date}，請以實戰角度審查 AI 選股系統今日的輸出品質。

## 市場背景（晨報）
{briefing_ctx or "（無晨報資料）"}

## AI 今日候選股 JSON
{picks_json}

## 近期品質回饋紀錄（供參考，避免重複提同一問題）
{prev_feedback or "（無近期紀錄）"}

---
## 任務

### Part 1 — 逐股審查
對每支候選股評估：
- data_completeness (0-10)：現有欄位資料是否足夠做當沖決策？
- trade_feasibility (0-10)：邏輯合理性、進/停損位合理嗎？
- missing_data：還缺哪些資訊才能更有把握（具體說明）
- comment：一句話評語

### Part 2 — Gemini 資料蒐集改善建議
列出 Gemini 在蒐集資料時應優先改善的地方：
- add_data_sources：應加入的資料來源（三大法人進出、融資券變化、外資買賣超明細等）
- add_indicators：應補充的技術指標（例：相對量能、ADX、VWAP 偏離度）
- add_themes：應追蹤的產業 / 題材（例：AI 伺服器、電動車、半導體設備）
- news_quality：新聞品質與即時性改善方向
- prompt_improvements：晨報 prompt 本身可如何調整以獲得更好分析

### Part 3 — 總結
- overall_score (0-10)
- top_pick：最推薦進場的股票代號（或 null）
- caution_pick：最應謹慎的股票代號（或 null）
- summary：50 字內整體評語

只輸出 JSON，不要加任何說明文字：
{{
  "picks_review": [
    {{
      "code": "代號",
      "data_completeness": 0,
      "trade_feasibility": 0,
      "missing_data": ["缺少項目"],
      "comment": "評語"
    }}
  ],
  "gemini_improvement": {{
    "add_data_sources": [],
    "add_indicators": [],
    "add_themes": [],
    "news_quality": "建議",
    "prompt_improvements": "建議"
  }},
  "overall_score": 0,
  "top_pick": null,
  "caution_pick": null,
  "summary": "評語"
}}"""

    print("  🤖 Claude 扮演資深交易員審查中...")
    try:
        from ai_client import call_sonnet
        raw = call_sonnet(prompt, max_tokens=4096)
    except Exception as e:
        print(f"  ❌ Claude API 失敗: {e}")
        return {}

    if not raw:
        print("  ❌ 回應為空")
        return {}

    # Parse JSON
    result: dict = {}
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            result = json.loads(m.group())
        else:
            result = {"raw": raw}
    except json.JSONDecodeError:
        result = {"raw": raw}

    # Enrich and save
    result["date"] = str(target_date)
    result["generated_at"] = datetime.now().isoformat()

    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _FEEDBACK_DIR / f"{target_date}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Terminal output ────────────────────────────────────────────────────────
    print(f"\n  📊 審查結果（整體評分 {result.get('overall_score','?')}/10）")
    print(f"     最推薦: {result.get('top_pick') or 'N/A'}")
    print(f"     最謹慎: {result.get('caution_pick') or 'N/A'}")
    print(f"     評語  : {result.get('summary','')}")

    if "picks_review" in result:
        print("\n  各股評分：")
        for pr in result["picks_review"]:
            print(f"     {pr.get('code')}  "
                  f"資料={pr.get('data_completeness')}/10  "
                  f"可行={pr.get('trade_feasibility')}/10  "
                  f"→ {pr.get('comment','')}")
            if pr.get("missing_data"):
                print(f"          缺: {', '.join(pr['missing_data'][:3])}")

    gi = result.get("gemini_improvement", {})
    if gi:
        print("\n  📝 Gemini 改善建議（下次蒐集資料請優先補充）：")
        for src in gi.get("add_data_sources", []):
            print(f"     + 資料來源: {src}")
        for ind in gi.get("add_indicators", []):
            print(f"     + 技術指標: {ind}")
        for th in gi.get("add_themes", []):
            print(f"     + 追蹤題材: {th}")
        if gi.get("news_quality"):
            print(f"     + 新聞品質: {gi['news_quality']}")
        if gi.get("prompt_improvements"):
            print(f"     + Prompt  : {gi['prompt_improvements']}")

    print(f"\n  💾 已儲存: {out_path}")

    # ── 更新 Gemini Context Hints 檔 ─────────────────────────────────────────
    _update_gemini_hints()

    # ── Telegram ──────────────────────────────────────────────────────────────
    if send_telegram and _CHAT_ID:
        try:
            from telegram_bot import send_text
            score = result.get("overall_score", "?")
            top   = result.get("top_pick") or "N/A"
            summary = result.get("summary", "")
            lines = [
                f"🧐 <b>資深交易員審查 [{target_date}]</b>",
                f"整體評分: {score}/10　最推薦: {top}",
                f"評語: {summary}",
            ]
            if "picks_review" in result:
                lines.append("")
                for pr in result["picks_review"]:
                    lines.append(
                        f"  {pr.get('code')}: 資料{pr.get('data_completeness')}/10 "
                        f"可行{pr.get('trade_feasibility')}/10"
                    )
            send_text(_CHAT_ID, "\n".join(lines))
            print("  ✅ 已發送 Telegram")
        except Exception as e:
            print(f"  ⚠️  Telegram 發送失敗: {e}")

    return result


def _update_gemini_hints() -> None:
    """Aggregate recent feedback into data/gemini_improvement_hints.txt for manual injection."""
    if not _FEEDBACK_DIR.exists():
        return
    files = sorted(_FEEDBACK_DIR.glob("*.json"), reverse=True)[:7]

    all_sources: dict[str, int] = {}
    all_indicators: dict[str, int] = {}
    all_themes: dict[str, int] = {}
    prompt_notes: list[str] = []

    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            gi = d.get("gemini_improvement", {})
            for s in gi.get("add_data_sources", []):
                all_sources[s] = all_sources.get(s, 0) + 1
            for i in gi.get("add_indicators", []):
                all_indicators[i] = all_indicators.get(i, 0) + 1
            for t in gi.get("add_themes", []):
                all_themes[t] = all_themes.get(t, 0) + 1
            if gi.get("prompt_improvements"):
                prompt_notes.append(f"[{f.stem}] {gi['prompt_improvements']}")
        except Exception:
            pass

    def _ranked(d: dict[str, int], top: int = 5) -> list[str]:
        return [k for k, _ in sorted(d.items(), key=lambda x: -x[1])][:top]

    lines = [
        "## 資深交易員建議（近 7 日累積）— 請在 Gemini 蒐集資料時優先補充",
        f"生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "### 應加入資料來源",
    ] + [f"- {s}" for s in _ranked(all_sources)] + [
        "",
        "### 應補充技術指標",
    ] + [f"- {i}" for i in _ranked(all_indicators)] + [
        "",
        "### 應追蹤產業題材",
    ] + [f"- {t}" for t in _ranked(all_themes)] + [
        "",
        "### Prompt 改善方向",
    ] + prompt_notes[-3:]

    hints_path = Path("data/gemini_improvement_hints.txt")
    hints_path.parent.mkdir(parents=True, exist_ok=True)
    hints_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  📄 Gemini 改善建議已更新: {hints_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. db — show DB state
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_db(target_date: date | None = None) -> None:
    if target_date is None:
        target_date = date.today()
    _sep(f"DB 狀態  [{target_date}]  ({_DB_PATH})")

    from research_db import init_db, load_daily_plan, load_daily_trades
    init_db(_DB_PATH)

    picks = load_daily_plan(target_date, _DB_PATH)
    trades = load_daily_trades(target_date, _DB_PATH)

    print(f"\n  daily_plans  [{target_date}]: {len(picks)} 筆")
    for p in picks:
        print(f"     {p.get('code')} {p.get('name',''):<6} "
              f"conf={p.get('confidence',0):.2f}  "
              f"target={p.get('target_price',0):.0f}  "
              f"stop={p.get('stop_loss_price',0):.0f}")

    print(f"\n  daily_trades [{target_date}]: {len(trades)} 筆")
    for t in trades:
        pnl = t.get("pnl")
        pnl_str = f"  pnl={pnl:.0f}" if pnl is not None else ""
        print(f"     {t.get('code')} {t.get('name',''):<6} "
              f"{t.get('action',''):<4} qty={t.get('quantity',0)} "
              f"price={t.get('price',0)}{pnl_str}")

    # Daytrading positions
    try:
        from daytrading_monitor import load_daytrading_positions
        positions = load_daytrading_positions()
        print(f"\n  daytrading_positions: {len(positions)} 筆")
        for p in positions:
            print(f"     {p.code} {p.name:<6} status={p.status}  "
                  f"dt_score={p.dt_score:.1f}  "
                  f"entry={p.entry_low:.1f}–{p.entry_high:.1f}")
    except Exception as e:
        print(f"\n  daytrading_positions: 讀取失敗 ({e})")


# ═══════════════════════════════════════════════════════════════════════════════
# 8. full pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_full(target_date: date | None = None) -> None:
    if target_date is None:
        target_date = date.today()
    _sep(f"完整流程  [{target_date}]")
    print("  Shioaji → 晨報選股 → 資深交易員審查 → Telegram")

    cmd_shioaji()
    approved = cmd_morning(target_date, send_telegram=False)
    review = cmd_review(target_date, send_telegram=True)

    _sep("完整流程完畢")
    print(f"  通過選股: {len(approved)} 檔")
    print(f"  審查評分: {review.get('overall_score','?')}/10")
    print(f"  評語    : {review.get('summary','')}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="QUANT·AI 開發測試工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("shioaji", help="測試 Shioaji 連線 + 即時報價")

    p_tg = sub.add_parser("telegram", help="發送 Telegram 測試訊息")
    p_tg.add_argument("msg", nargs="?", help="自訂訊息內容")

    p_m = sub.add_parser("morning", help="手動執行 8:30 晨報選股")
    p_m.add_argument("date", nargs="?", help="YYYY-MM-DD (預設今天)")
    p_m.add_argument("--telegram", action="store_true", help="發送 Telegram 通知")

    p_dt = sub.add_parser("daytrade", help="手動執行 8:30 當沖預測報告")
    p_dt.add_argument("--telegram", action="store_true", help="發送 Telegram 通知")

    p_cf = sub.add_parser("confirm", help="手動執行 9:05 開盤確認")
    p_cf.add_argument("--telegram", action="store_true", help="發送 Telegram 通知")

    p_rv = sub.add_parser("review", help="資深交易員 AI 審查 + Gemini 改善建議")
    p_rv.add_argument("date", nargs="?", help="YYYY-MM-DD (預設今天)")
    p_rv.add_argument("--telegram", action="store_true", help="發送審查結果到 Telegram")

    p_db = sub.add_parser("db", help="查看 DB 當日狀態")
    p_db.add_argument("date", nargs="?", help="YYYY-MM-DD (預設今天)")

    p_fu = sub.add_parser("full", help="完整流程：morning → review → telegram")
    p_fu.add_argument("date", nargs="?", help="YYYY-MM-DD (預設今天)")

    args = parser.parse_args()

    if args.cmd == "shioaji":
        cmd_shioaji()
    elif args.cmd == "telegram":
        cmd_telegram(getattr(args, "msg", None))
    elif args.cmd == "morning":
        cmd_morning(_parse_date(getattr(args, "date", None)),
                    send_telegram=args.telegram)
    elif args.cmd == "daytrade":
        cmd_daytrade(send_telegram=args.telegram)
    elif args.cmd == "confirm":
        cmd_confirm(send_telegram=args.telegram)
    elif args.cmd == "review":
        cmd_review(_parse_date(getattr(args, "date", None)),
                   send_telegram=args.telegram)
    elif args.cmd == "db":
        cmd_db(_parse_date(getattr(args, "date", None)))
    elif args.cmd == "full":
        cmd_full(_parse_date(getattr(args, "date", None)))


if __name__ == "__main__":
    main()
