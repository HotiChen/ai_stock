"""
morning_briefing.py
每日早晨簡報系統

流程：
  1. collect_market_data()  ← RSS + TWSE API + Yahoo Finance
  2. build_briefing_prompt() ← 組 Gemini prompt
  3. call_gemini()           ← Gemini 2.5 Pro 2M context 分析
  4. _parse_briefing_response() ← 解析 JSON 回傳
  5. save_briefing()         ← 存 data/morning_briefings/YYYY-MM-DD.json

盤中追加：append_intraday_update()
盤後填入：fill_post_market()
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ai_client import call_gemini

log = logging.getLogger(__name__)

_DEFAULT_DIR = "data/morning_briefings"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class MarketData:
    date:             date
    # 台灣大盤
    twse_index:       Optional[float]   # 加權指數收盤
    twse_volume:      Optional[float]   # 成交金額（億元）
    foreign_net:      Optional[float]   # 外資淨買（億元，正=買）
    trust_net:        Optional[float]   # 投信淨買（億元）
    margin_balance:   Optional[float]   # 融資餘額（億元）
    # 美股 / 恐慌指標
    sp500:            Optional[float]
    nasdaq:           Optional[float]
    sox:              Optional[float]   # 費城半導體指數
    vix:              Optional[float]   # 恐慌指數
    # 新聞
    news_items:       list[str] = field(default_factory=list)


@dataclass
class MorningBriefing:
    date:               date
    generated_at:       datetime
    market_data:        MarketData
    ai_analysis:        str
    market_outlook:     str              # "bullish" | "bearish" | "neutral"
    key_risks:          list[str]
    key_opportunities:  list[str]
    sector_focus:       list[str]
    intraday_updates:   list[str] = field(default_factory=list)
    post_market:        Optional[str] = None


# ── Data collection ───────────────────────────────────────────────────────────

def _fetch_rss_news(feeds: Optional[list[str]] = None) -> list[str]:
    """RSS 新聞標題。依賴 feedparser（可選；失敗時回空列表）。"""
    if feeds is None:
        feeds = [
            "https://www.cnyes.com/rss/news/headline.xml",       # 鉅亨網
            "https://tw.stock.yahoo.com/rss",                    # Yahoo 奇摩
        ]
    items: list[str] = []
    try:
        import feedparser  # type: ignore
        for url in feeds:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:5]:
                    title   = entry.get("title", "")
                    summary = entry.get("summary", "")[:80]
                    if title:
                        items.append(f"{title}｜{summary}" if summary else title)
            except Exception as e:
                log.warning("RSS fetch failed for %s: %s", url, e)
    except ImportError:
        log.warning("feedparser not installed, skipping RSS")
    return items


def _find_latest_trading_date(max_lookback: int = 7) -> Optional[date]:
    """往前找最近的 TWSE 交易日（最多往前 max_lookback 天）。"""
    try:
        import requests  # type: ignore
        for i in range(max_lookback):
            d = date.today() - __import__("datetime").timedelta(days=i)
            date_str = d.strftime("%Y%m%d")
            url = (
                f"https://www.twse.com.tw/rwd/zh/fund/BFI82U"
                f"?response=json&dayDate={date_str}&type=day"
            )
            resp = requests.get(url, timeout=8, verify=False)
            data = resp.json()
            if data.get("stat") == "OK":
                return d
    except Exception as e:
        log.warning("_find_latest_trading_date failed: %s", e)
    return None


def _fetch_twse_institutional(target_date: Optional[date] = None) -> dict:
    """TWSE 三大法人買賣超彙總（BFI82U）。
    target_date 為 None 時自動找最近交易日。
    回傳：foreign_net / trust_net（億元，正=買超）、trading_date。
    """
    result: dict = {"foreign_net": None, "trust_net": None, "trading_date": None}
    try:
        import requests  # type: ignore
        if target_date is None:
            target_date = _find_latest_trading_date()
        if target_date is None:
            return result

        date_str = target_date.strftime("%Y%m%d")
        url = (
            f"https://www.twse.com.tw/rwd/zh/fund/BFI82U"
            f"?response=json&dayDate={date_str}&type=day"
        )
        resp  = requests.get(url, timeout=10, verify=False)
        data  = resp.json()
        rows  = data.get("data", [])
        result["trading_date"] = target_date.isoformat()

        for row in rows:
            name  = str(row[0])
            net_s = str(row[3]).replace(",", "").replace(" ", "")
            try:
                net_val = round(float(net_s) / 1e8, 2)   # 元 → 億
            except Exception:
                continue
            if name.startswith("外資及陸資"):
                result["foreign_net"] = net_val
            elif name == "投信":
                result["trust_net"] = net_val
    except Exception as e:
        log.warning("TWSE institutional fetch failed: %s", e)
    return result


def _fetch_us_market() -> dict:
    """美股指數 + VIX + 台股大盤 via yfinance。"""
    result: dict = {
        "twse_index": None, "twse_volume": None,
        "sp500": None, "nasdaq": None, "sox": None, "vix": None,
    }
    try:
        import yfinance as yf  # type: ignore
        symbols = {
            "twse_index": "^TWII",   # 台股加權指數
            "sp500":      "^GSPC",
            "nasdaq":     "^IXIC",
            "sox":        "^SOX",
            "vix":        "^VIX",
        }
        for key, symbol in symbols.items():
            try:
                ticker = yf.Ticker(symbol)
                hist   = ticker.history(period="5d")
                if not hist.empty:
                    result[key] = round(float(hist["Close"].iloc[-1]), 2)
                    # 台股成交量（單位：張）→ 換算為億元（近似）
                    if key == "twse_index" and "Volume" in hist.columns:
                        # TWII Volume 是股數，不是金額，先留空由 TWSE API 提供
                        pass
            except Exception as e:
                log.warning("yfinance %s failed: %s", symbol, e)
    except ImportError:
        log.warning("yfinance not installed, skipping market data")
    return result


def collect_market_data(target_date: Optional[date] = None) -> MarketData:
    """整合 RSS + TWSE + 美股（含台股大盤），回傳 MarketData。"""
    if target_date is None:
        target_date = date.today()

    news  = _fetch_rss_news()
    twse  = _fetch_twse_institutional()   # 自動找最近交易日
    us    = _fetch_us_market()

    return MarketData(
        date=target_date,
        twse_index=us.get("twse_index"),   # ^TWII via yfinance
        twse_volume=us.get("twse_volume"),
        foreign_net=twse.get("foreign_net"),
        trust_net=twse.get("trust_net"),
        margin_balance=None,               # 融資餘額需另外抓
        sp500=us.get("sp500"),
        nasdaq=us.get("nasdaq"),
        sox=us.get("sox"),
        vix=us.get("vix"),
        news_items=news,
    )


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_briefing_prompt(market_data: MarketData) -> str:
    """將 MarketData 組成 Gemini prompt。"""

    def _fmt(val: Optional[float], fmt: str = ",.1f") -> str:
        return format(val, fmt) if val is not None else "N/A"

    news_text = "\n".join(f"  - {n}" for n in market_data.news_items) or "  （無新聞資料）"

    return f"""你是一位資深台股市場分析師，每天早上 8 點整合全球市場數據，給出今日台股操作建議。

=== 今日市場數據（{market_data.date}）===

【台灣大盤】
- 加權指數：{_fmt(market_data.twse_index)} 點
- 成交金額：{_fmt(market_data.twse_volume)} 億元
- 外資淨買賣：{_fmt(market_data.foreign_net)} 億元（正=買超）
- 投信淨買賣：{_fmt(market_data.trust_net)} 億元
- 融資餘額：{_fmt(market_data.margin_balance)} 億元

【美股 & 恐慌指標】
- S&P 500：{_fmt(market_data.sp500)}
- NASDAQ：{_fmt(market_data.nasdaq)}
- 費城半導體指數（SOX）：{_fmt(market_data.sox)}
- VIX 恐慌指數：{_fmt(market_data.vix)}

【今日重要新聞】
{news_text}

=== 分析任務 ===
根據以上數據，給出今日台股簡報。請特別注意：
1. SOX 走勢對台灣半導體族群的影響
2. 外資動向（連買/連賣幾日、金額大小）
3. VIX 是否反映風險偏好
4. 重要新聞催化劑

只回傳 JSON，格式如下：
{{
  "ai_analysis": "200 字以內的今日市場綜合分析（中文）",
  "market_outlook": "bullish 或 bearish 或 neutral",
  "key_risks": ["風險1", "風險2", "風險3"],
  "key_opportunities": ["機會1", "機會2"],
  "sector_focus": ["今日重點產業1", "今日重點產業2"]
}}"""


# ── Parser ────────────────────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict:
    """先嘗試直接 parse；失敗則找 ```json ... ``` 或 {{...}} 區塊。"""
    try:
        return json.loads(raw)
    except Exception:
        pass
    # markdown code block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    # bare object
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    raise ValueError("no valid JSON found")


def _parse_briefing_response(
    raw: str,
    target_date: date,
    market_data: Optional[MarketData] = None,
) -> Optional[MorningBriefing]:
    """解析 Gemini JSON 回應，回傳 MorningBriefing。失敗回傳 None。"""
    try:
        data = _extract_json(raw)
        return MorningBriefing(
            date=target_date,
            generated_at=datetime.now(),
            market_data=market_data or MarketData(
                date=target_date,
                twse_index=None, twse_volume=None,
                foreign_net=None, trust_net=None, margin_balance=None,
                sp500=None, nasdaq=None, sox=None, vix=None,
            ),
            ai_analysis=str(data.get("ai_analysis", "")),
            market_outlook=str(data.get("market_outlook", "neutral")),
            key_risks=list(data.get("key_risks", [])),
            key_opportunities=list(data.get("key_opportunities", [])),
            sector_focus=list(data.get("sector_focus", [])),
            intraday_updates=[],
            post_market=None,
        )
    except Exception as e:
        log.error("_parse_briefing_response failed: %s", e)
        return None


# ── Main entry ────────────────────────────────────────────────────────────────

def generate_morning_briefing(
    target_date: Optional[date] = None,
) -> Optional[MorningBriefing]:
    """完整流程：收集資料 → Gemini 分析 → 回傳 MorningBriefing。"""
    if target_date is None:
        target_date = date.today()

    try:
        market_data = collect_market_data(target_date)
    except Exception as e:
        log.error("collect_market_data failed: %s", e)
        return None

    prompt = build_briefing_prompt(market_data)
    try:
        raw = call_gemini(prompt)
        if not raw:
            log.error("generate_morning_briefing: call_gemini returned empty")
            return None
        briefing = _parse_briefing_response(raw, target_date, market_data)
        if briefing is None:
            log.error("generate_morning_briefing: parse failed, raw[:200]=%s", raw[:200])
        return briefing
    except Exception as e:
        log.error("generate_morning_briefing exception: %s", e)
        return None


# ── Persistence ───────────────────────────────────────────────────────────────

def _briefing_path(target_date: date, briefing_dir: str) -> Path:
    return Path(briefing_dir) / f"{target_date.isoformat()}.json"


def _briefing_to_dict(b: MorningBriefing) -> dict:
    md = b.market_data
    return {
        "date":             b.date.isoformat(),
        "generated_at":     b.generated_at.isoformat(),
        "market_data": {
            "date":            md.date.isoformat(),
            "twse_index":      md.twse_index,
            "twse_volume":     md.twse_volume,
            "foreign_net":     md.foreign_net,
            "trust_net":       md.trust_net,
            "margin_balance":  md.margin_balance,
            "sp500":           md.sp500,
            "nasdaq":          md.nasdaq,
            "sox":             md.sox,
            "vix":             md.vix,
            "news_items":      md.news_items,
        },
        "ai_analysis":        b.ai_analysis,
        "market_outlook":     b.market_outlook,
        "key_risks":          b.key_risks,
        "key_opportunities":  b.key_opportunities,
        "sector_focus":       b.sector_focus,
        "intraday_updates":   b.intraday_updates,
        "post_market":        b.post_market,
    }


def _briefing_from_dict(d: dict) -> MorningBriefing:
    md_d = d["market_data"]
    md = MarketData(
        date=date.fromisoformat(md_d["date"]),
        twse_index=md_d.get("twse_index"),
        twse_volume=md_d.get("twse_volume"),
        foreign_net=md_d.get("foreign_net"),
        trust_net=md_d.get("trust_net"),
        margin_balance=md_d.get("margin_balance"),
        sp500=md_d.get("sp500"),
        nasdaq=md_d.get("nasdaq"),
        sox=md_d.get("sox"),
        vix=md_d.get("vix"),
        news_items=md_d.get("news_items", []),
    )
    return MorningBriefing(
        date=date.fromisoformat(d["date"]),
        generated_at=datetime.fromisoformat(d["generated_at"]),
        market_data=md,
        ai_analysis=d.get("ai_analysis", ""),
        market_outlook=d.get("market_outlook", "neutral"),
        key_risks=d.get("key_risks", []),
        key_opportunities=d.get("key_opportunities", []),
        sector_focus=d.get("sector_focus", []),
        intraday_updates=d.get("intraday_updates", []),
        post_market=d.get("post_market"),
    )


def save_briefing(
    briefing: MorningBriefing,
    briefing_dir: str = _DEFAULT_DIR,
    path: Optional[str] = None,
) -> str:
    """存到 briefing_dir/YYYY-MM-DD.json，回傳完整路徑。
    若提供 path，直接寫入該路徑（忽略 briefing_dir）。"""
    if path is not None:
        target = Path(path)
    else:
        target = _briefing_path(briefing.date, briefing_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_briefing_to_dict(briefing), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(target)


def load_briefing(
    target_date: date,
    briefing_dir: str = _DEFAULT_DIR,
) -> Optional[MorningBriefing]:
    """讀取指定日期的簡報。不存在回傳 None。"""
    path = _briefing_path(target_date, briefing_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _briefing_from_dict(data)
    except Exception:
        return None


def append_intraday_update(
    target_date: date,
    update: str,
    briefing_dir: str = _DEFAULT_DIR,
) -> None:
    """追加盤中快報（10:00 / 12:00 呼叫）。"""
    path = _briefing_path(target_date, briefing_dir)
    if not path.exists():
        raise FileNotFoundError(f"找不到 {target_date} 的簡報：{path}")
    briefing = load_briefing(target_date, briefing_dir)
    briefing.intraday_updates.append(update)
    save_briefing(briefing, briefing_dir)


def fill_post_market(
    target_date: date,
    analysis: str,
    briefing_dir: str = _DEFAULT_DIR,
) -> None:
    """盤後填入驗證分析。"""
    path = _briefing_path(target_date, briefing_dir)
    if not path.exists():
        raise FileNotFoundError(f"找不到 {target_date} 的簡報：{path}")
    briefing = load_briefing(target_date, briefing_dir)
    briefing.post_market = analysis
    save_briefing(briefing, briefing_dir)
