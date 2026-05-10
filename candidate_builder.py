"""
candidate_builder.py — 統一候選股建立邏輯（H11）

原邏輯在 morning_strategy.py 與 app.py 中完整重複。
此模組為唯一來源（Single Source of Truth），兩邊均 import 這裡。

Pipeline：
  1. 從 ai_log.jsonl 取最近 10 筆 AI 選股紀錄
  2. 從主題池隨機補充至 30 支
  3. 補齊股票名稱（Shioaji / config.STOCK_NAMES）
  4. 即時股價（Shioaji snapshot，每支最多等 6 秒）
  5. 籌碼資料（三大法人 + 融資融券）
  6. 計算 chip_score 並排序
"""
from __future__ import annotations

import json
import logging
import random
import threading
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

_AI_LOG_PATH   = Path("data/ai_log.jsonl")
_MAX_AI_PICKS  = 10
_MAX_TOTAL     = 30
_SNAPSHOT_TIMEOUT = 6   # Shioaji snapshot 每支最多等秒數


# ── 內部工具 ──────────────────────────────────────────────────────────────────

def _get_name_map(api) -> dict[str, str]:
    """從 Shioaji 取得股票名稱對照表；api 為 None 時退回 config.STOCK_NAMES。"""
    import config
    result = dict(config.STOCK_NAMES)
    if api is None:
        return result
    for exchange in ("TSE", "OTC", "OES"):
        try:
            for c in getattr(api.Contracts.Stocks, exchange, []):
                result[c.code] = c.name
        except Exception:
            pass
    return result


def _fetch_yfinance_prices(codes: list[str]) -> dict[str, float]:
    """用 yfinance 批次取台股前一日收盤價，作為 Shioaji 盤前/模擬模式的 fallback。

    台股代號規則：
      - 純數字 4 碼 → 加 ".TW"（上市）
      - 數字開頭含字母（如 00878B）→ 加 ".TWO"（上櫃）
      - 已含 dot 的跳過
    失敗時回傳空 dict（不影響主流程）。
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    def _suffix(code: str) -> str:
        if "." in code:
            return code
        # 零股 ETF 或上櫃標的常有字母結尾（如 00878B）
        if code.isdigit():
            return f"{code}.TW"
        return f"{code}.TWO"

    tickers = {code: _suffix(code) for code in codes}
    symbols = list(tickers.values())

    result: dict[str, float] = {}
    try:
        data = yf.download(
            symbols,
            period="2d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        close = data.get("Close", data)  # yfinance >= 0.2 returns MultiIndex

        for code, symbol in tickers.items():
            try:
                col = close[symbol] if symbol in close.columns else close
                last = col.dropna().iloc[-1]
                if last > 0:
                    result[code] = float(last)
            except Exception:
                pass
    except Exception as e:
        log.warning("yfinance batch download failed: %s", e)
    return result


def _fetch_snapshot(api, code: str) -> dict:
    """執行緒安全的單支股票 snapshot，timeout 6 秒，失敗回傳空 dict。"""
    result: dict = {}

    def _run() -> None:
        try:
            contract = api.Contracts.Stocks.get(code)
            if contract:
                snaps = api.snapshots([contract])
                if snaps:
                    result["close"]       = float(snaps[0].close)
                    result["change_rate"] = float(snaps[0].change_rate)
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=_SNAPSHOT_TIMEOUT)
    return result


# ── 主要函數 ──────────────────────────────────────────────────────────────────

def build_candidates(api=None) -> list[dict]:
    """
    建立候選股清單。

    Args:
        api: Shioaji API 物件（可 None，None 時股價留 0.0）。

    Returns:
        依 chip_score 降序排列的候選股列表，每筆包含：
        code, name, close, change_rate, analysis, chip_score
    """
    from themes import THEME_GROUPS
    from chip_data import (
        fetch_institutional_investors,
        fetch_margin_trading,
        get_continuous_buy_days,
    )

    raw: list[dict] = []

    # ── 1. 從 ai_log 抓最近紀錄 ──────────────────────────────────────────────
    if _AI_LOG_PATH.exists():
        with _AI_LOG_PATH.open(encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    d = e.get("decision", {})
                    if d.get("stock_code"):
                        raw.append({
                            "code":        d["stock_code"],
                            "name":        d.get("stock_code"),
                            "close":       0.0,
                            "change_rate": 0.0,
                            "analysis":    d.get("reason", ""),
                        })
                except Exception:
                    pass

    seen: set[str] = set()
    deduped: list[dict] = []
    for c in reversed(raw):
        if c["code"] not in seen:
            seen.add(c["code"])
            deduped.append(c)
        if len(deduped) >= _MAX_AI_PICKS:
            break

    # ── 2. 從所有主題池隨機補充 ──────────────────────────────────────────────
    all_theme_codes = list({
        code
        for codes in THEME_GROUPS.values()
        for code in codes
    })
    random.shuffle(all_theme_codes)
    for code in all_theme_codes:
        if code not in seen and len(deduped) < _MAX_TOTAL:
            seen.add(code)
            deduped.append({
                "code":        code,
                "name":        code,
                "close":       0.0,
                "change_rate": 0.0,
                "analysis":    "主題池補充（無個股分析，請依技術面自行評估）",
            })

    # ── 3. 補齊名稱與即時股價 ─────────────────────────────────────────────────
    nm = _get_name_map(api)
    for c in deduped:
        c["name"] = nm.get(c["code"], c["code"])
        if api:
            snap = _fetch_snapshot(api, c["code"])
            c["close"]       = snap.get("close",       0.0)
            c["change_rate"] = snap.get("change_rate", 0.0)

    # ── 3b. yfinance 補價（Shioaji 盤前/模擬模式拿不到即時價格時的 fallback）──
    zero_codes = [c["code"] for c in deduped if c.get("close", 0.0) == 0.0]
    if zero_codes:
        yf_prices = _fetch_yfinance_prices(zero_codes)
        for c in deduped:
            if c.get("close", 0.0) == 0.0 and c["code"] in yf_prices:
                c["close"] = yf_prices[c["code"]]
                log.debug("yfinance fallback price: %s → %.2f", c["code"], c["close"])

    # ── 3c. 技術指標（M4：yfinance 批次取 6 個月日線 → MA/RSI/KD/BB/量比）────
    try:
        from technical_indicators import fetch_indicators_yfinance_batch, format_indicators_text
        all_codes = [c["code"] for c in deduped]
        ind_map = fetch_indicators_yfinance_batch(all_codes)
        for c in deduped:
            ind = ind_map.get(c["code"])
            c["indicators_text"] = format_indicators_text(ind) if ind else ""
        log.info("技術指標：%d/%d 支取得成功", len(ind_map), len(all_codes))
    except Exception as e:
        log.warning("技術指標取得失敗：%s", e)
        for c in deduped:
            c.setdefault("indicators_text", "")

    # ── 4. 抓籌碼資料 ────────────────────────────────────────────────────────
    today_str = date.today().strftime("%Y%m%d")
    try:
        inst_data   = fetch_institutional_investors(today_str)
        margin_data = fetch_margin_trading(today_str)
        log.info("籌碼資料：三大法人 %d 筆，融資融券 %d 筆", len(inst_data), len(margin_data))
    except Exception as e:
        log.warning("籌碼資料取得失敗：%s", e)
        inst_data   = {}
        margin_data = {}

    # ── 5. 標注籌碼 + 計算排序分數 ───────────────────────────────────────────
    for c in deduped:
        code = c["code"]
        chip_parts: list[str] = []
        chip_score = 0.0

        inst = inst_data.get(code)
        if inst:
            foreign_net = inst.get("foreign_net", 0)
            trust_net   = inst.get("investment_trust_net", 0)
            if foreign_net != 0:
                chip_parts.append(f"外資{'+' if foreign_net > 0 else ''}{foreign_net:.0f}張")
                chip_score += min(foreign_net / 500, 5)
            if trust_net != 0:
                chip_parts.append(f"投信{'+' if trust_net > 0 else ''}{trust_net:.0f}張")
                chip_score += min(trust_net / 100, 3)
            if inst.get("total_net", 0) > 0:
                chip_score += 1

        try:
            cont = get_continuous_buy_days(code, today_str, days=5)
            foreign_days = cont.get("foreign_continuous_buy", 0)
            trust_days   = cont.get("investment_trust_continuous_buy", 0)
            if foreign_days >= 2:
                chip_parts.append(f"外資連買{foreign_days}天")
                chip_score += foreign_days * 1.5
            if trust_days >= 2:
                chip_parts.append(f"投信連買{trust_days}天")
                chip_score += trust_days * 1.0
        except Exception:
            pass

        margin = margin_data.get(code)
        if margin:
            margin_prev   = margin.get("margin_prev", 0)
            margin_change = margin.get("margin_change", 0)
            if margin_prev > 0:
                margin_pct = margin_change / margin_prev * 100
                if abs(margin_pct) >= 3:
                    chip_parts.append(f"融資{'↑' if margin_pct > 0 else '↓'}{abs(margin_pct):.1f}%")
                if margin_pct > 10:
                    chip_score -= 2

        if chip_parts:
            chip_summary  = "｜".join(chip_parts)
            orig          = c.get("analysis", "")
            c["analysis"] = f"【籌碼】{chip_summary}　{orig}".strip()
        c["chip_score"] = chip_score

    # ── 6. 依籌碼分數排序 ────────────────────────────────────────────────────
    deduped.sort(key=lambda x: x.get("chip_score", 0), reverse=True)

    n_chip  = len([c for c in deduped if c.get("chip_score", 0) > 0])
    n_theme = len([c for c in deduped if "主題池補充" in c.get("analysis", "")])
    log.info("候選股建立完成：共 %d 支｜有籌碼=%d｜主題補充=%d", len(deduped), n_chip, n_theme)
    return deduped
