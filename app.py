import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import shioaji as sj
import shioaji.constant as sc
import streamlit as st
from dotenv import load_dotenv

import config
from alerts import Alert, AlertDirection, filter_triggered_alerts
from dashboard import DashboardData, build_dashboard, get_portfolio_summary
from logic import add_indicators, build_chart, get_yesterday_close, normalize_kbars_df
from market_scan import batch_fetch_snapshots, get_top_gainers, get_top_losers, get_top_volume, search_stocks
from strategies import calc_position_pnl, check_dip_buy, trailing_stop_triggered
from themes import THEME_GROUPS, calc_theme_stats, get_theme_detail_rows, get_theme_momentum_label, rank_themes
from stock_detail import build_signal_badges, get_fundamental_verdict
from daytrading_config import DaytradingConfig, load_daytrading_config, save_daytrading_config
from trading_rules import (
    TradingRules,
    calc_max_quantity,
    load_rules,
    save_rules,
    summarize_chat_to_strategy,
    validate_decision,
)
from stock_research import (
    analyze_stock_ai,
    fetch_stock_fundamentals,
    fetch_stock_news,
    format_fundamentals_text,
)
from chat_agent import (
    ChatMessage,
    build_trading_advisor_prompt,
    call_anthropic_chat,
    get_quick_prompts,
)
from trades import TradeAction, TradeRecord, calc_total_pnl, calc_win_rate, load_trades, save_trade
from strategy_planner import (
    PlanSet,
    generate_strategy_plans,
)
from sim_plan_store import (
    list_sim_plans,
    load_sim_plan,
    save_sim_plan,
    delete_sim_plan,
)
from daily_tracker import (
    DailyTrackRecord,
    PlanResult,
    load_day_record,
    save_day_record,
    update_plan_result,
    list_day_records,
    delete_day_record,
)
from sim_engine import (
    compare_plans,
    generate_sim_report,
    open_position,
    close_position,
    update_unrealized,
    get_portfolio,
)
from strategy_tracker import (
    DailyEntry,
    StrategyGoal,
    calc_daily_target,
    calc_progress,
    check_daily_target,
    generate_daily_review,
    load_daily_entries,
    load_goal,
    save_daily_entry,
    save_goal,
)
from sim_position_store import SimPosition, save_sim_positions, load_sim_positions, clear_sim_positions
from sim_settlement import settle_positions, fetch_closing_prices
from notifier import notify_settlement
from telegram_bot import send_morning_push

load_dotenv()

st.set_page_config(page_title="台股交易系統", page_icon="📈", layout="wide")

try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=5 * 60 * 1000, key="auto_refresh")
except ImportError:
    pass

# ── Session state ─────────────────────────────────────────────────────────────

for key, default in [
    ("alerts", []),
    ("nav_stock", None),       # 目前查看的股票代碼
    ("nav_theme", None),       # 目前選的題材
    ("dashboard_theme", None), # 總覽頁展開的題材
    ("chat_messages", []),     # AI 顧問對話記錄
    ("rules", None),           # TradingRules（lazy load）
    ("buys_today", 0),         # 今日買入次數
    ("sells_today", 0),        # 今日賣出次數
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── API helpers ───────────────────────────────────────────────────────────────

@st.cache_resource
def get_api():
    simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"
    api = sj.Shioaji(simulation=simulation)
    api.login(
        api_key=os.getenv("SHIOAJI_API_KEY"),
        secret_key=os.getenv("SHIOAJI_SECRET_KEY"),
        fetch_contract=True,
    )
    return api


@st.cache_data(ttl=3600)
def get_name_map() -> dict[str, str]:
    result = dict(config.STOCK_NAMES)   # start with hardcoded fallback
    api = get_api()
    for exchange in ("TSE", "OTC", "OES"):
        try:
            for c in getattr(api.Contracts.Stocks, exchange, []):
                result[c.code] = c.name
        except Exception:
            pass
    return result


@st.cache_data(ttl=300)
def fetch_snapshot(code: str):
    api = get_api()
    contract = api.Contracts.Stocks.get(code)
    if not contract:
        return None, None
    try:
        snaps = api.snapshots([contract])
        return contract, snaps[0] if snaps else None
    except Exception as e:
        print(f"Failed to fetch snapshot for {code}: {e}")
        return contract, None


@st.cache_data(ttl=300)
def fetch_kbars(code: str, days: int = 120) -> pd.DataFrame:
    api = get_api()
    contract = api.Contracts.Stocks.get(code)
    if not contract:
        return pd.DataFrame()
    # 原本一次查 days 天（預設 120），超過 Shioaji 的 30 天上限會回 400。
    import shioaji_history as sh
    df = sh.fetch_daily_as_kbars_df(api, code, days=days)
    return normalize_kbars_df(df) if not df.empty else df


@st.cache_data(ttl=60)
def fetch_positions() -> list[dict]:
    api = get_api()
    name_map = get_name_map()
    try:
        raw = api.list_positions(api.stock_account)
        result = []
        for pos in raw:
            snap_data = fetch_theme_snaps.__wrapped__ if hasattr(fetch_theme_snaps, "__wrapped__") else None
            _, snap = fetch_snapshot(pos.code)
            current = snap.close if snap else pos.price
            result.append({
                "stock_code": pos.code,
                "name": name_map.get(pos.code, pos.code),
                "cost": pos.price,
                "current": current,
                "quantity": pos.quantity,
            })
        return result
    except Exception:
        return []


@st.cache_data(ttl=300)
def fetch_theme_snaps(theme: str) -> dict[str, dict]:
    import threading
    api = get_api()
    name_map = get_name_map()
    codes = THEME_GROUPS.get(theme, [])
    contracts = [c for c in (api.Contracts.Stocks.get(cd) for cd in codes) if c]
    if not contracts:
        return {}
    result = {}
    _snaps: list = []

    def _fetch():
        try:
            _snaps.extend(api.snapshots(contracts))
        except Exception as e:
            print(f"fetch_theme_snaps error {theme}: {e}")

    t = threading.Thread(target=_fetch, daemon=True)  # daemon=True：主程式不等它
    t.start()
    t.join(timeout=6)   # 最多等 6 秒，之後不管它繼續跑
    if not t.is_alive():
        # thread 正常完成
        for snap in _snaps:
            result[snap.code] = {
                "name": name_map.get(snap.code, snap.code),
                "close": snap.close, "change_rate": snap.change_rate,
                "change_price": snap.change_price, "total_volume": snap.total_volume,
                "open": snap.open, "high": snap.high, "low": snap.low,
            }
    else:
        print(f"fetch_theme_snaps timeout: {theme}（超過 6 秒，略過）")
    return result


@st.cache_data(ttl=300)
def fetch_all_theme_stats():
    all_stats = []
    for theme, codes in THEME_GROUPS.items():
        snaps = fetch_theme_snaps(theme)
        all_stats.append(calc_theme_stats(theme, codes, snaps))
    return rank_themes(all_stats)


@st.cache_data(ttl=300)
def fetch_market_top(n: int = 100) -> dict[str, dict]:
    api = get_api()
    name_map = get_name_map()
    popular = [
        "2330","2317","2454","2382","3711","2308","2303","2379","6770","3034",
        "2881","2882","2883","2884","2886","2603","2609","2615","0050","0056",
        "00631L","00878","00919","00929","2002","2409","3481","2345","4966",
        "2344","6415","3533","5347","2408","6669","3231","3008","2356","1590",
        "6285","2049","6515","3413","2338","5285","2327","2351","4736","3257",
    ]
    result = batch_fetch_snapshots(api, popular[:n])
    for code, snap in result.items():
        snap["name"] = name_map.get(code, code)
    return result


@st.cache_data(ttl=1800)
def fetch_stock_research(code: str, name: str) -> dict:
    """Fetch news + fundamentals for a stock (cached 30 min)."""
    news         = fetch_stock_news(code, name, max_items=6)
    fundamentals = fetch_stock_fundamentals(code)
    return {"news": news, "fundamentals": fundamentals}


def load_latest_ai_log() -> dict | None:
    path = Path("data/ai_log.jsonl")
    if not path.exists():
        return None
    last = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                last = json.loads(line)
            except Exception:
                pass
    return last


# ── 共用元件 ──────────────────────────────────────────────────────────────────

def stock_card(code: str, name: str, close: float, change_rate: float,
               change_price: float, volume: int, nav_key: str) -> bool:
    """Full-width clickable stock row. Returns True when clicked."""
    color = "#ef5350" if change_rate > 0 else "#26a69a" if change_rate < 0 else "#888"
    sign = "+" if change_rate > 0 else ""
    label = (
        f"{name}　{code}　"
        f"{close:.2f}　{sign}{change_rate:.2f}%　量 {volume:,}"
    )
    clicked = st.button(label, key=nav_key, use_container_width=True)
    return clicked


def show_stock_detail(code: str, days: int = 120):
    """Render full stock detail view."""
    contract, snap = fetch_snapshot(code)
    if not contract or not snap:
        st.error(f"無法載入 {code}")
        return
    color = "#ef5350" if snap.change_price > 0 else "#26a69a" if snap.change_price < 0 else "#888"
    sign = "+" if snap.change_price > 0 else ""
    name_map = get_name_map()
    st.markdown(
        f"## {name_map.get(code, contract.name)}"
        f"<span style='color:#666;font-size:15px'> {code}</span>&nbsp;&nbsp;"
        f"<span style='color:{color};font-size:26px'>{snap.close:.2f}</span>&nbsp;"
        f"<span style='color:{color}'>{sign}{snap.change_price:.2f} ({sign}{snap.change_rate:.2f}%)</span>",
        unsafe_allow_html=True,
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("開盤", f"{snap.open:.2f}")
    c2.metric("最高", f"{snap.high:.2f}")
    c3.metric("最低", f"{snap.low:.2f}")
    c4.metric("成交量", f"{snap.total_volume:,}")
    c5.metric("昨收", f"{get_yesterday_close(snap):.2f}")

    df = fetch_kbars(code, days)
    indicators = {}
    if not df.empty and len(df) >= 5:
        df = add_indicators(df)
        st.plotly_chart(build_chart(df), use_container_width=True)
        last = df.iloc[-1]
        ti = st.columns(6)
        for col, (label, val) in zip(ti, {
            "MA5": last.get("MA5"), "MA20": last.get("MA20"), "MA60": last.get("MA60"),
            "RSI(14)": last.get("RSI"), "MACD": last.get("MACD"), "Signal": last.get("MACD_signal"),
        }.items()):
            if pd.notna(val):
                delta = f"{last['Close'] - val:+.2f}" if label.startswith("MA") else None
                col.metric(label, f"{val:.2f}", delta)
                indicators[label] = round(float(val), 2)
            else:
                col.metric(label, "N/A")
        indicators["Close"] = float(last["Close"])

    # ── 研究資料（自動載入）────────────────────────────────────────
    st.divider()
    name_map = get_name_map()
    stock_name = name_map.get(code, code)

    with st.spinner("載入基本面與新聞..."):
        research = fetch_stock_research(code, stock_name)
    news         = research["news"]
    fundamentals = research["fundamentals"]

    # ── 信號徽章 ──────────────────────────────────────────────────
    badges = build_signal_badges(fundamentals, indicators)
    verdict = get_fundamental_verdict(fundamentals)

    _COLOR_MAP = {"green": "#26a69a", "red": "#ef5350", "yellow": "#ffa726", "blue": "#42a5f5", "gray": "#888"}
    badge_html = "".join(
        f"<span style='background:{_COLOR_MAP[b.color]};color:#fff;"
        f"padding:4px 10px;border-radius:12px;margin:3px;font-size:13px;"
        f"display:inline-block' title='{b.description}'>{b.label}</span>"
        for b in badges
    )
    st.markdown(
        f"<div style='margin-bottom:6px'>{badge_html}</div>"
        f"<div style='color:#aaa;font-size:13px'>📊 基本面評估：{verdict}</div>",
        unsafe_allow_html=True,
    )

    st.divider()

    # ── 基本面 + 新聞並排 ─────────────────────────────────────────
    col_f, col_n = st.columns([1, 1])

    with col_f:
        st.markdown("**📋 基本面**")
        pe    = fundamentals.pe_ratio
        eps   = fundamentals.eps
        dy    = fundamentals.dividend_yield
        rg    = fundamentals.revenue_growth
        hi    = fundamentals.week52_high
        lo    = fundamentals.week52_low
        pm    = fundamentals.profit_margin

        mf1, mf2 = st.columns(2)
        mf1.metric("本益比 P/E", f"{pe:.1f}" if pe else "N/A")
        mf2.metric("EPS",       f"{eps:.2f}" if eps else "N/A")
        mf3, mf4 = st.columns(2)
        mf3.metric("殖利率",    f"{dy*100:.2f}%" if dy else "N/A")
        mf4.metric("營收成長",  f"{rg*100:+.1f}%" if rg else "N/A")
        mf5, mf6 = st.columns(2)
        mf5.metric("52W 高",   f"{hi:.2f}" if hi else "N/A")
        mf6.metric("52W 低",   f"{lo:.2f}" if lo else "N/A")
        if pm:
            st.caption(f"淨利率：{pm*100:.1f}%")

    with col_n:
        st.markdown("**📰 近期新聞**")
        if news:
            for h in news:
                st.markdown(
                    f"<div style='padding:6px 0;border-bottom:1px solid #2a2a2a;font-size:13px'>{h}</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("無近期新聞")

    # ── AI 分析（按鈕觸發）───────────────────────────────────────
    st.divider()
    col_btn, col_hint = st.columns([1, 4])
    run_analysis = col_btn.button("🤖 AI 綜合分析", key=f"research_{code}", type="primary")
    col_hint.caption("結合新聞、基本面、技術面，請 AI 給出買賣建議（約 15–30 秒）")

    if run_analysis:
        with st.spinner("AI 分析中..."):
            result = analyze_stock_ai(code, stock_name, news, fundamentals, indicators)

        action = result["action"]
        action_label = {"buy": "🟢 建議買進", "sell": "🔴 建議賣出", "hold": "⬜ 建議觀望"}.get(action, action)
        bg = {"buy": "#1a3a2a", "sell": "#3a1a1a"}.get(action, "#1e1e2e")
        st.markdown(
            f"<div style='background:{bg};padding:16px;border-radius:8px;margin-bottom:12px'>"
            f"<div style='font-size:20px;font-weight:bold'>{action_label}</div>"
            f"<div style='color:#ccc;margin-top:6px'>{result['reason']}</div>"
            f"<div style='color:#666;font-size:12px;margin-top:4px'>信心 {result['confidence']}/10</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        tp = result.get("target_price")
        sl = result.get("stop_loss")
        ra1, ra2 = st.columns(2)
        ra1.metric("目標價",   f"{tp:.2f}" if tp else "N/A")
        ra2.metric("建議停損", f"{sl:.2f}" if sl else "N/A")


# ── Tab 1: 總覽 Dashboard ─────────────────────────────────────────────────────

def tab_dashboard():
    positions = fetch_positions()
    trades = load_trades()
    ai_entry = load_latest_ai_log()
    alert_triggered = len(filter_triggered_alerts(
        st.session_state.alerts,
        {a.stock_code: (fetch_snapshot(a.stock_code)[1].close
                        if fetch_snapshot(a.stock_code)[1] else 0)
         for a in st.session_state.alerts}
    ))

    data = build_dashboard(positions, trades, alert_triggered, ai_entry, config.BUDGET)

    # ── 資產總覽 ──────────────────────────────────────────────────
    pnl_color = "#ef5350" if data.total_pnl > 0 else "#26a69a" if data.total_pnl < 0 else "#888"
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("可用現金", f"${data.cash:,.0f}")
    c2.metric("持倉市值", f"${data.total_market_value:,.0f}")
    c3.metric("未實現損益", f"${data.total_pnl:+,.0f}", f"{data.total_pnl_pct:+.2f}%")
    c4.metric("歷史勝率", f"{data.win_rate*100:.1f}%")

    st.divider()

    col_ai, col_alert = st.columns(2)

    # ── 最新 AI 決策 ──────────────────────────────────────────────
    with col_ai:
        st.subheader("🤖 最新 AI 決策")
        action_bg = {"buy": "#1a3a2a", "sell": "#3a1a1a"}.get(data.latest_ai_action, "#1a1a2a")
        action_label = {"buy": "🟢 買進", "sell": "🔴 賣出", "hold": "⬜ 持有"}.get(data.latest_ai_action, "—")
        if ai_entry:
            ts = ai_entry.get("timestamp", "")[:19].replace("T", " ")
            st.markdown(
                f"<div style='background:{action_bg};padding:14px;border-radius:8px'>"
                f"<div style='font-size:18px;font-weight:bold'>{action_label}</div>"
                f"<div style='color:#ccc;margin-top:6px'>{data.latest_ai_reason[:80]}</div>"
                f"<div style='color:#666;font-size:12px;margin-top:4px'>{ts}</div>"
                f"</div>", unsafe_allow_html=True,
            )
        else:
            st.info("尚無 AI 記錄，執行 simulate.py 產生資料")

    # ── 警示狀態 ──────────────────────────────────────────────────
    with col_alert:
        st.subheader("🔔 警示狀態")
        total_a = len(st.session_state.alerts)
        if total_a == 0:
            st.info("尚未設定任何警示")
        else:
            c1, c2 = st.columns(2)
            c1.metric("觸發中", data.active_alerts, delta_color="off")
            c2.metric("總警示數", total_a, delta_color="off")

    st.divider()

    # ── 熱門題材快照 ─────────────────────────────────────────────
    st.subheader("🏷️ 題材動能")

    # 若已展開某個題材，顯示題材 Dashboard
    if st.session_state.dashboard_theme:
        theme = st.session_state.dashboard_theme
        if st.button("← 返回總覽"):
            st.session_state.dashboard_theme = None
            st.rerun()
        codes = THEME_GROUPS[theme]
        snaps = fetch_theme_snaps(theme)
        stats = calc_theme_stats(theme, codes, snaps)
        momentum = get_theme_momentum_label(stats.avg_change_pct)
        sign = "+" if stats.avg_change_pct > 0 else ""
        avg_color = "#ef5350" if stats.avg_change_pct > 0 else "#26a69a" if stats.avg_change_pct < 0 else "#888"
        st.markdown(
            f"<div style='background:#1a1a1a;padding:16px;border-radius:10px;margin-bottom:12px'>"
            f"<div style='font-size:20px;font-weight:bold'>🏷️ {theme}</div>"
            f"<div style='color:{avg_color};font-size:28px;margin-top:6px'>"
            f"{sign}{stats.avg_change_pct:.2f}%　{momentum}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        dc1, dc2, dc3, dc4 = st.columns(4)
        dc1.metric("上漲", stats.advance_count)
        dc2.metric("下跌", stats.decline_count)
        dc3.metric("總成交量", f"{stats.total_volume:,}")
        dc4.metric("有資料股數", f"{stats.stock_count} / {len(codes)}")
        if stats.strongest_stock:
            strong_name = snaps.get(stats.strongest_stock, {}).get("name", stats.strongest_stock)
            weak_name = snaps.get(stats.weakest_stock, {}).get("name", stats.weakest_stock)
            cs, cw = st.columns(2)
            cs.success(f"🔥 最強：{strong_name}（{stats.strongest_stock}）　"
                       f"{snaps[stats.strongest_stock]['change_rate']:+.2f}%")
            cw.error(f"❄️ 最弱：{weak_name}（{stats.weakest_stock}）　"
                     f"{snaps[stats.weakest_stock]['change_rate']:+.2f}%")
        st.divider()
        rows = get_theme_detail_rows(theme, codes, snaps)
        for row in rows:
            if stock_card(
                code=row["code"], name=row["name"],
                close=row["close"], change_rate=row["change_rate"],
                change_price=row["change_price"], volume=row["total_volume"],
                nav_key=f"dash_theme_stock_{row['code']}",
            ):
                st.session_state.nav_stock = row["code"]
                st.session_state.nav_theme = theme
                st.rerun()
        return

    # 題材卡片列表（可點擊）
    try:
        all_stats = fetch_all_theme_stats()
        cols = st.columns(3)
        for i, stats in enumerate(all_stats[:6]):
            col = cols[i % 3]
            momentum = get_theme_momentum_label(stats.avg_change_pct)
            sign = "+" if stats.avg_change_pct > 0 else ""
            color = "#ef5350" if stats.avg_change_pct > 0 else "#26a69a" if stats.avg_change_pct < 0 else "#888"
            with col:
                st.markdown(
                    f"<div style='background:#1a1a1a;padding:10px 12px 4px 12px;"
                    f"border-radius:8px 8px 0 0'>"
                    f"<div style='font-weight:bold'>{stats.name}</div>"
                    f"<div style='color:{color};font-size:18px'>{sign}{stats.avg_change_pct:.2f}%　{momentum}</div>"
                    f"<div style='color:#aaa;font-size:12px;margin-bottom:6px'>"
                    f"漲{stats.advance_count} 跌{stats.decline_count}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                if st.button("查看明細 →", key=f"dash_theme_{stats.name}", use_container_width=True):
                    st.session_state.dashboard_theme = stats.name
                    st.rerun()
    except Exception:
        st.warning("題材資料載入中...")

    # ── 持倉快照 ─────────────────────────────────────────────────
    if positions:
        st.divider()
        st.subheader("💼 持倉快照")
        rows = []
        for pos in positions:
            pnl = calc_position_pnl(pos["cost"], pos["current"], pos["quantity"])
            rows.append({
                "名稱": pos["name"], "代碼": pos["stock_code"],
                "現價": pos["current"], "成本": pos["cost"],
                "損益": pnl["pnl_amount"], "損益%": pnl["pnl_pct"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ── Tab 2: 市場 ───────────────────────────────────────────────────────────────

def tab_market():
    # 麵包屑
    crumbs = ["📊 市場"]
    if st.session_state.nav_theme:
        crumbs.append(st.session_state.nav_theme)
    if st.session_state.nav_stock:
        crumbs.append(st.session_state.nav_stock)
    st.markdown("　›　".join(f"**{c}**" for c in crumbs))

    # 個股詳細（第三層）
    if st.session_state.nav_stock:
        if st.button("← 返回"):
            st.session_state.nav_stock = None
            st.rerun()
        show_stock_detail(st.session_state.nav_stock)
        return

    # 題材 Dashboard（第二層）
    if st.session_state.nav_theme:
        if st.button("← 返回題材"):
            st.session_state.nav_theme = None
            st.rerun()

        theme = st.session_state.nav_theme
        codes = THEME_GROUPS[theme]
        snaps = fetch_theme_snaps(theme)
        stats = calc_theme_stats(theme, codes, snaps)
        momentum = get_theme_momentum_label(stats.avg_change_pct)
        sign = "+" if stats.avg_change_pct > 0 else ""
        avg_color = "#ef5350" if stats.avg_change_pct > 0 else "#26a69a" if stats.avg_change_pct < 0 else "#888"

        # ── 題材摘要 ──────────────────────────────────────────
        st.markdown(
            f"<div style='background:#1a1a1a;padding:16px;border-radius:10px;margin-bottom:12px'>"
            f"<div style='font-size:20px;font-weight:bold'>🏷️ {theme}</div>"
            f"<div style='color:{avg_color};font-size:28px;margin-top:6px'>"
            f"{sign}{stats.avg_change_pct:.2f}%　{momentum}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("上漲", stats.advance_count)
        c2.metric("下跌", stats.decline_count)
        c3.metric("總成交量", f"{stats.total_volume:,}")
        c4.metric("有資料股數", f"{stats.stock_count} / {len(codes)}")

        if stats.strongest_stock:
            strong_name = snaps.get(stats.strongest_stock, {}).get("name", stats.strongest_stock)
            weak_name = snaps.get(stats.weakest_stock, {}).get("name", stats.weakest_stock)
            col_s, col_w = st.columns(2)
            col_s.success(f"🔥 最強：{strong_name}（{stats.strongest_stock}）"
                          f"　{snaps[stats.strongest_stock]['change_rate']:+.2f}%")
            col_w.error(f"❄️ 最弱：{weak_name}（{stats.weakest_stock}）"
                        f"　{snaps[stats.weakest_stock]['change_rate']:+.2f}%")

        st.divider()
        st.markdown("**個股明細**")

        # ── 個股列表 ──────────────────────────────────────────
        rows = get_theme_detail_rows(theme, codes, snaps)
        for row in rows:
            if stock_card(
                code=row["code"], name=row["name"],
                close=row["close"], change_rate=row["change_rate"],
                change_price=row["change_price"], volume=row["total_volume"],
                nav_key=f"theme_stock_{row['code']}",
            ):
                st.session_state.nav_stock = row["code"]
                st.rerun()
        return

    # 第一層：市場總覽
    market_tab, theme_tab, search_tab = st.tabs(["📈 熱門排行", "🏷️ 題材", "🔍 搜尋"])

    # ── 熱門排行 ──────────────────────────────────────────────────
    with market_tab:
        with st.spinner("載入市場資料..."):
            snaps = fetch_market_top(50)

        if not snaps:
            st.warning("市場資料暫無（非交易時段）")
        else:
            g_tab, l_tab, v_tab = st.tabs(["🔥 漲幅王", "❄️ 跌幅王", "⚡ 爆量股"])

            def render_rank(rows: list[dict], prefix: str):
                for row in rows:
                    if stock_card(
                        code=row["code"], name=row["name"],
                        close=row["close"], change_rate=row["change_rate"],
                        change_price=row["change_price"], volume=row["total_volume"],
                        nav_key=f"{prefix}_{row['code']}",
                    ):
                        st.session_state.nav_stock = row["code"]
                        st.rerun()

            with g_tab:
                render_rank(get_top_gainers(snaps, 15), "gain")
            with l_tab:
                render_rank(get_top_losers(snaps, 15), "lose")
            with v_tab:
                render_rank(get_top_volume(snaps, 15), "vol")

    # ── 題材 ──────────────────────────────────────────────────────
    with theme_tab:
        if st.button("🔄 更新題材"):
            fetch_all_theme_stats.clear()
            fetch_theme_snaps.clear()
            st.rerun()
        all_stats = fetch_all_theme_stats()
        for stats in all_stats:
            momentum = get_theme_momentum_label(stats.avg_change_pct)
            sign = "+" if stats.avg_change_pct > 0 else ""
            color = "#ef5350" if stats.avg_change_pct > 0 else "#26a69a" if stats.avg_change_pct < 0 else "#888"
            label = (
                f"{stats.name}　"
                f"{sign}{stats.avg_change_pct:.2f}%　{momentum}　"
                f"漲{stats.advance_count} 跌{stats.decline_count}　量{stats.total_volume:,}"
            )
            if st.button(label, key=f"theme_{stats.name}", use_container_width=True):
                st.session_state.nav_theme = stats.name
                st.session_state.nav_stock = None
                st.rerun()

    # ── 搜尋 ──────────────────────────────────────────────────────
    with search_tab:
        query = st.text_input("輸入股票代碼或名稱", placeholder="例：台積電 或 2330")
        snaps_all = fetch_market_top(50)
        results = search_stocks(snaps_all, query)
        if query and not results:
            # 直接查單支
            if stock_card(
                code=query, name=query, close=0, change_rate=0,
                change_price=0, volume=0, nav_key=f"search_direct_{query}",
            ):
                st.session_state.nav_stock = query
                st.rerun()
        else:
            for row in results:
                if stock_card(
                    code=row["code"], name=row["name"],
                    close=row["close"], change_rate=row["change_rate"],
                    change_price=row["change_price"], volume=row["total_volume"],
                    nav_key=f"search_{row['code']}",
                ):
                    st.session_state.nav_stock = row["code"]
                    st.rerun()


# ── Tab 3: 投資組合 ───────────────────────────────────────────────────────────

def tab_portfolio():
    pos_tab, pnl_tab = st.tabs(["💼 持倉", "📋 損益記錄"])

    with pos_tab:
        if st.button("🔄 更新持倉"):
            fetch_positions.clear()
            st.rerun()
        positions = fetch_positions()
        if not positions:
            st.info("目前無持倉")
        else:
            summary = get_portfolio_summary(positions)
            c1, c2, c3 = st.columns(3)
            c1.metric("總成本", f"${summary['total_cost']:,.0f}")
            c2.metric("總市值", f"${summary['total_market_value']:,.0f}")
            c3.metric("損益", f"${summary['total_pnl']:+,.0f}", f"{summary['total_pnl_pct']:+.2f}%")
            st.divider()
            for pos in positions:
                pnl = calc_position_pnl(pos["cost"], pos["current"], pos["quantity"])
                color = "#ef5350" if pnl["pnl_amount"] > 0 else "#26a69a"
                if stock_card(
                    code=pos["stock_code"], name=pos["name"],
                    close=pos["current"], change_rate=pnl["pnl_pct"],
                    change_price=pnl["pnl_amount"], volume=0,
                    nav_key=f"pos_{pos['stock_code']}",
                ):
                    st.session_state.nav_stock = pos["stock_code"]
                    st.session_state.nav_theme = None
                    st.rerun()

    with pnl_tab:
        records = load_trades()
        if not records:
            st.info("尚無交易記錄")
        else:
            total = calc_total_pnl(records)
            win_rate = calc_win_rate(records)
            sells = sum(1 for r in records if r.action == TradeAction.SELL)
            c1, c2, c3 = st.columns(3)
            c1.metric("總損益", f"${total:+,.0f}")
            c2.metric("勝率", f"{win_rate*100:.1f}%")
            c3.metric("已平倉筆數", sells)
            st.divider()
            rows = [{"日期": r.trade_date, "代碼": r.stock_code, "動作": r.action.value,
                     "數量": int(r.quantity), "價格": r.price, "損益": r.pnl}
                    for r in sorted(records, key=lambda r: r.trade_date, reverse=True)]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ── Tab 4: 交易 ───────────────────────────────────────────────────────────────

def tab_trade():
    order_tab, strategy_tab = st.tabs(["🛒 手動下單", "⚙️ 策略監控"])

    with order_tab:
        c1, c2 = st.columns(2)
        with c1:
            code = st.text_input("股票代碼", max_chars=6, key="order_code")
            action = st.radio("方向", ["買進", "賣出"], horizontal=True)
            quantity = st.number_input("數量（張）", min_value=1, value=1)
        with c2:
            price_type = st.radio("價格類型", ["限價", "市價"], horizontal=True)
            price = st.number_input("限價", min_value=0.0, step=0.5, disabled=(price_type == "市價"))
            st.caption("⚠️ 模擬盤不會真實下單")
        if st.button("送出委託", type="primary"):
            if not code:
                st.error("請輸入股票代碼")
            else:
                try:
                    api = get_api()
                    contract = api.Contracts.Stocks.get(code)
                    if not contract:
                        st.error(f"找不到 {code}")
                    else:
                        sj_action = sc.Action.Buy if action == "買進" else sc.Action.Sell
                        order = api.Order(
                            price=price if price_type == "限價" else 0,
                            quantity=quantity,
                            action=sj_action,
                            price_type=sc.StockPriceType.LMT if price_type == "限價" else sc.StockPriceType.MKT,
                            order_type=sc.OrderType.ROD,
                        )
                        trade = api.place_order(contract, order)
                        st.success(f"委託成功！{trade.order.id}")
                        save_trade(TradeRecord(code, TradeAction.BUY if action == "買進" else TradeAction.SELL,
                                               quantity * 1000, price, 0.0, date.today()))
                except Exception as e:
                    st.error(f"下單失敗：{e}")

    with strategy_tab:
        with st.expander("🔴 移動停損監控"):
            ts_code = st.text_input("股票代碼", key="ts_code")
            col1, col2, col3 = st.columns(3)
            ts_entry = col1.number_input("成本價", min_value=0.0, step=0.5)
            ts_peak = col2.number_input("最高價", min_value=0.0, step=0.5)
            ts_pct = col3.slider("停損 %", 1, 20, 5)
            if ts_code and ts_entry > 0:
                _, snap = fetch_snapshot(ts_code)
                if snap:
                    triggered = trailing_stop_triggered(ts_entry, snap.close, ts_peak, ts_pct)
                    if triggered:
                        st.error(f"⚠️ 停損觸發！現價 {snap.close:.2f}")
                    else:
                        drop = (ts_peak - snap.close) / ts_peak * 100 if ts_peak > ts_entry else 0
                        st.success(f"✅ 現價 {snap.close:.2f}，從高點回撤 {drop:.1f}%")

        with st.expander("🟢 抄底監控"):
            dip_code = st.text_input("股票代碼", key="dip_code")
            col1, col2 = st.columns(2)
            dip_target = col1.number_input("目標買入價", min_value=0.0, step=0.5)
            dip_threshold = col2.slider("容許誤差 %", 0, 10, 2)
            if dip_code and dip_target > 0:
                _, snap = fetch_snapshot(dip_code)
                if snap:
                    if check_dip_buy(snap.close, dip_target, dip_threshold):
                        st.success(f"✅ 現價 {snap.close:.2f} 符合買入條件！")
                    else:
                        st.info(f"現價 {snap.close:.2f}，目標 {dip_target:.2f}")


# ── Tab 5: 警示 ───────────────────────────────────────────────────────────────

def tab_alerts():
    st.subheader("新增警示")
    c1, c2, c3 = st.columns(3)
    a_code = c1.text_input("股票代碼", key="alert_code")
    a_price = c2.number_input("目標價", min_value=0.0, step=0.5)
    a_dir = c3.radio("方向", ["突破", "跌破"], horizontal=True)
    if st.button("新增"):
        if a_code and a_price > 0:
            direction = AlertDirection.ABOVE if a_dir == "突破" else AlertDirection.BELOW
            st.session_state.alerts.append(Alert(a_code, a_price, direction))
            st.success(f"已新增：{a_code} {a_dir} {a_price:.2f}")

    st.divider()
    if not st.session_state.alerts:
        st.info("尚無警示")
        return

    prices = {}
    for alert in st.session_state.alerts:
        if alert.stock_code not in prices:
            _, snap = fetch_snapshot(alert.stock_code)
            if snap:
                prices[alert.stock_code] = snap.close

    triggered_set = {(a.stock_code, a.price, a.direction)
                     for a in filter_triggered_alerts(st.session_state.alerts, prices)}
    to_remove = []
    for i, alert in enumerate(st.session_state.alerts):
        is_hit = (alert.stock_code, alert.price, alert.direction) in triggered_set
        current = prices.get(alert.stock_code, "—")
        label = "突破" if alert.direction == AlertDirection.ABOVE else "跌破"
        col_info, col_del = st.columns([5, 1])
        with col_info:
            msg = f"{'🔔' if is_hit else '⏳'} {alert.stock_code} {label} {alert.price:.2f}　現價：{current}"
            (st.error if is_hit else st.info)(msg)
        if col_del.button("✕", key=f"del_{i}"):
            to_remove.append(i)
    for i in reversed(to_remove):
        st.session_state.alerts.pop(i)
    if to_remove:
        st.rerun()


# ── Tab 6: AI 決策 ────────────────────────────────────────────────────────────

def _send_chat(user_input: str, positions: list[dict], ai_log: dict | None):
    """Append user message, call Anthropic Sonnet, append assistant reply."""
    msgs: list[ChatMessage] = st.session_state.chat_messages

    if not msgs:
        sys_prompt = build_trading_advisor_prompt(positions=positions, ai_log=ai_log)
        msgs.append(ChatMessage(role="system", content=sys_prompt))

    msgs.append(ChatMessage(role="user", content=user_input))

    try:
        reply = call_anthropic_chat(msgs) or "（AI 未回應）"
    except Exception as e:
        reply = f"連線失敗：{e}"

    msgs.append(ChatMessage(role="assistant", content=reply))
    st.session_state.chat_messages = msgs


_GOAL_PATH    = "data/strategy_goal.json"
_ENTRIES_PATH = "data/strategy_entries.jsonl"
_RESEARCH_DB  = "data/research.db"
_PID_FILE     = "data/executor.pid"


def _executor_running() -> bool:
    pid_path = Path(_PID_FILE)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)   # 0 = just check, doesn't actually send signal
        return True
    except (ProcessLookupError, PermissionError, ValueError):
        pid_path.unlink(missing_ok=True)
        return False


def _start_executor() -> None:
    if _executor_running():
        return
    Path("data").mkdir(exist_ok=True)
    script = Path(__file__).parent / "run_executor.py"
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=open("data/executor.log", "a"),
        stderr=subprocess.STDOUT,
    )
    Path(_PID_FILE).write_text(str(proc.pid))


def _stop_executor() -> None:
    pid_path = Path(_PID_FILE)
    if not pid_path.exists():
        return
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 15)   # SIGTERM
    except Exception:
        pass
    pid_path.unlink(missing_ok=True)


def _render_strategy_tracker():
    st.markdown("### 📈 策略追蹤")

    goal = load_goal(_GOAL_PATH)

    # ── 設定目標 ──────────────────────────────────────────────────
    with st.expander("⚙️ 設定 / 修改目標", expanded=goal is None):
        with st.form("goal_form"):
            col1, col2 = st.columns(2)
            init_cap = col1.number_input(
                "初始本金（元）", min_value=1_000.0, value=float(goal.initial_capital) if goal else 30_000.0,
                step=1_000.0, format="%.0f",
            )
            multiplier = col2.number_input(
                "目標倍數", min_value=1.01, value=float(goal.target_multiplier) if goal else 2.0,
                step=0.1, format="%.2f",
            )
            col3, col4 = st.columns(2)
            start = col3.date_input("開始日", value=goal.start_date if goal else date.today())
            end   = col4.date_input("結束日", value=goal.end_date   if goal else date.today() + timedelta(days=30))
            approach = st.text_area(
                "做法描述",
                value=goal.approach if goal else "短線技術分析 + AI 輔助決策",
                height=80,
            )

            st.divider()
            st.caption("🔧 進階風控設定（選填）")
            col5, col6, col7 = st.columns(3)
            stop_loss_pct_val = col5.number_input(
                "停損 %（0=不設）",
                min_value=0.0, max_value=50.0,
                value=float(goal.stop_loss_pct or 0.0) if goal else 0.0,
                step=0.5, format="%.1f",
                help="每筆持倉虧損超過此 % 時停損，0 表示不設停損",
            )
            max_pos_val = col6.number_input(
                "最多持倉檔數（0=不限）",
                min_value=0, max_value=20,
                value=int(goal.max_positions or 0) if goal else 0,
                step=1,
                help="同時持有的最大股票數量，0 表示不限",
            )
            allow_frac_val = col7.checkbox(
                "允許零股",
                value=bool(goal.allow_fractional) if goal else False,
                help="整張買不起時可使用零股方式進場",
            )
            allow_day_trade_val = col7.checkbox(
                "允許當沖",
                value=bool(goal.allow_day_trade) if goal else False,
                help="允許 AI 建議當日沖銷操作（買進當天即賣出）",
            )

            if st.form_submit_button("💾 儲存目標", use_container_width=True):
                if end <= start:
                    st.error("結束日必須晚於開始日")
                else:
                    new_goal = StrategyGoal(
                        target_multiplier=multiplier,
                        start_date=start,
                        end_date=end,
                        initial_capital=init_cap,
                        approach=approach,
                        stop_loss_pct=stop_loss_pct_val if stop_loss_pct_val > 0 else None,
                        max_positions=int(max_pos_val) if max_pos_val > 0 else None,
                        allow_fractional=allow_frac_val,
                        allow_day_trade=allow_day_trade_val,
                    )
                    save_goal(new_goal, _GOAL_PATH)
                    st.success("目標已儲存")
                    st.rerun()

    if goal is None:
        st.info("請先設定策略目標")
        return

    # ── 進度概覽 ──────────────────────────────────────────────────
    today = date.today()
    daily_target = calc_daily_target(goal)

    entries = load_daily_entries(_ENTRIES_PATH)
    today_entry = next((e for e in entries if e.date == today), None)
    today_pnl   = today_entry.pnl if today_entry else 0.0

    total_cumulative_pnl = sum(e.pnl for e in entries)
    current_value = goal.initial_capital + total_cumulative_pnl

    progress = calc_progress(goal, current_value, today)

    # 目標概覽卡片
    target_met = check_daily_target(today_pnl, daily_target)
    met_icon   = "✅" if target_met else "❌"
    on_track_icon = "🟢 超前進度" if progress.on_track else "🔴 落後進度"
    compl_color   = "#26a69a" if progress.completion_pct >= 50 else "#ef5350"

    st.markdown(
        f"<div style='background:#1e1e2e;padding:16px;border-radius:10px;margin-bottom:12px'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
        f"<div><span style='font-size:18px;font-weight:bold'>目標：{goal.initial_capital:,.0f} 元 × {goal.target_multiplier}x "
        f"= {goal.target_value:,.0f} 元</span><br>"
        f"<span style='color:#aaa;font-size:13px'>{goal.approach}</span></div>"
        f"<div style='text-align:right;color:{compl_color};font-size:24px;font-weight:bold'>"
        f"{progress.completion_pct:.1f}%</div></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("累積損益", f"{total_cumulative_pnl:+,.0f} 元",
              delta=f"{progress.total_pnl_pct:+.1f}%")
    m2.metric("今日損益", f"{today_pnl:+,.0f} 元",
              delta=f"{met_icon} 每日目標 {daily_target:,.0f}")
    m3.metric("剩餘天數", f"{progress.days_remaining} 天",
              delta=f"已過 {progress.days_elapsed} 天")
    m4.metric("每日需賺", f"{progress.required_daily_pnl:,.0f} 元",
              delta=on_track_icon)

    st.progress(min(progress.completion_pct / 100, 1.0))

    # ── 執行狀態 ──────────────────────────────────────────────────
    from research_db import init_db as _init_db, load_active_execution as _load_exec
    from strategy_executor import get_execution_status as _get_status, load_execution_records
    _init_db(_RESEARCH_DB)
    active_exec = _load_exec(_RESEARCH_DB)
    if active_exec:
        status = _get_status(active_exec.execution_id, _RESEARCH_DB)
        plan_label = {"aggressive": "🔥 衝刺版", "balanced": "⚖️ 均衡版",
                      "conservative": "🛡️ 保守版"}.get(active_exec.plan_type, active_exec.plan_type)
        records = load_execution_records(active_exec.execution_id, _RESEARCH_DB)
        today_records = [r for r in records if r.recorded_at.date() == today]
        e1, e2, e3, e4 = st.columns(4)
        running = _executor_running()
        e1.metric("執行中計劃", plan_label)
        e2.metric("今日操作", f"{len(today_records)} 筆")
        e3.metric("模擬累積損益", f"{status.total_simulated_pnl:+,.0f} 元" if status else "—")
        e4.metric("背景程式", "🟢 運行中" if running else "🔴 已停止")
        sb1, sb2 = st.columns(2)
        if sb1.button("⏹️ 停止執行計劃", use_container_width=True):
            from strategy_executor import stop_execution as _stop
            _stop(active_exec.execution_id, _RESEARCH_DB)
            _stop_executor()
            st.session_state.pop("_active_exec_id", None)
            st.success("計劃已停止")
            st.rerun()
        if not running and sb2.button("▶️ 重新啟動背景程式", use_container_width=True):
            _start_executor()
            st.success("背景程式已重新啟動")
            st.rerun()
        if today_records:
            with st.expander(f"📋 今日執行記錄（{len(today_records)} 筆）"):
                for rec in today_records:
                    icon = "🟢" if rec.action == "buy" else "🔴" if rec.action == "sell" else "⬜"
                    st.markdown(f"{icon} `{rec.recorded_at.strftime('%H:%M')}` "
                                f"**{rec.action.upper()} {rec.code} {rec.name}** "
                                f"{rec.quantity}張 @{rec.price:.0f}　{rec.reason[:50]}")

    # ── 三計劃比較報告 ────────────────────────────────────────────
    exec_map = st.session_state.get("_exec_map", {})
    if len(exec_map) > 1:
        st.divider()
        st.markdown("#### 📊 三套計劃模擬比較報告")
        total_pnl_used = sum(e.pnl for e in entries)
        init_cap = goal.initial_capital
        reports = compare_plans(exec_map, init_cap, _RESEARCH_DB)
        medal = ["🥇", "🥈", "🥉"]
        rep_cols = st.columns(len(reports))
        for i, (col, report) in enumerate(zip(rep_cols, reports)):
            plan_label = {"aggressive": "🔥 衝刺版", "balanced": "⚖️ 均衡版",
                          "conservative": "🛡️ 保守版"}.get(report.plan_type, report.plan_type)
            pnl_color = "#ef5350" if report.total_pnl > 0 else "#26a69a"
            with col:
                st.markdown(
                    f"<div style='background:#1e1e2e;padding:14px;border-radius:10px;text-align:center'>"
                    f"<div style='font-size:20px'>{medal[i]}</div>"
                    f"<div style='font-size:15px;font-weight:bold'>{plan_label}</div>"
                    f"<div style='font-size:22px;color:{pnl_color};margin:8px 0'>"
                    f"{report.return_pct:+.2f}%</div>"
                    f"<div style='color:#aaa;font-size:12px'>"
                    f"損益 {report.total_pnl:+,.0f} 元<br>"
                    f"已實現 {report.realized_pnl:+,.0f}<br>"
                    f"未實現 {report.unrealized_pnl:+,.0f}<br>"
                    f"勝率 {report.win_rate:.0f}%　{report.total_trades} 筆</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    st.divider()

    # ── AI 策略計劃 ────────────────────────────────────────────────
    st.markdown("#### 🤖 AI 投資計劃生成")
    st.caption("AI 會分析候選股票後，生成衝刺版 / 均衡版 / 保守版三套計劃")

    def _build_candidates() -> list[dict]:
        """候選股建立：委派給 candidate_builder（Single Source of Truth，H11）。"""
        from candidate_builder import build_candidates as _bc
        return _bc(api=get_api())

    col_gen, col_clear = st.columns([3, 1])
    if col_gen.button("🚀 生成三套投資計劃", use_container_width=True, type="primary"):
        print("開始執行 ai 策略生成", flush=True)
        st.components.v1.html('<script>console.log("開始執行 ai 策略生成")</script>', height=0)
        deduped = _build_candidates()
        print(f"[策略生成] 開始｜current_value={current_value:,.0f}｜候選股 {len(deduped)} 支：{[c['code'] for c in deduped]}")

        with st.spinner("AI 分析中，約需 30–60 秒..."):
            print("[策略生成] 呼叫 Claude API...")
            # 載入現有持倉，讓 AI 針對持倉給出 hold/add/reduce/close/switch 建議
            from portfolio import SimulatedPortfolio
            _portfolio_path = "data/portfolio.json"
            _portfolio = SimulatedPortfolio.load(_portfolio_path) if Path(_portfolio_path).exists() else None
            if _portfolio and _portfolio.get_positions():
                print(f"[策略生成] 傳入持倉 {len(_portfolio.get_positions())} 檔，AI 將考慮換股建議", flush=True)
            plan_set = generate_strategy_plans(goal, current_value, deduped, portfolio=_portfolio)
        if plan_set is None:
            print("[策略生成] ❌ generate_strategy_plans 回傳 None")
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                st.error("生成失敗，請確認 ANTHROPIC_API_KEY 已設定")
            else:
                st.error(f"生成失敗（API Key 已設定，但 AI 回應解析失敗）。請查看終端機 log 取得詳細錯誤。")
        else:
            picks = {pt: len(getattr(plan_set, pt).picks) for pt in ("aggressive", "balanced", "conservative")}
            print(f"[策略生成] ✅ 成功｜picks={picks}")
            st.session_state["_plan_set"] = plan_set
            try:
                saved_path = save_sim_plan(plan_set)
                st.toast(f"計劃已存檔：{saved_path.name}", icon="💾")
            except Exception:
                pass
            # 推播三套策略到 Telegram，讓使用者選擇
            try:
                send_morning_push(briefing=None, plan_set=plan_set, starting_capital=current_value)
                st.toast("📨 三套策略已推播到 Telegram，請在 Telegram 選擇執行哪套", icon="📨")
            except Exception as _te:
                print(f"[Telegram] 推播失敗：{_te}", flush=True)
            st.rerun()

    if col_clear.button("🗑️ 清除", use_container_width=True):
        st.session_state.pop("_plan_set", None)
        st.rerun()

    if "_plan_set" in st.session_state:
        plan_set: PlanSet = st.session_state["_plan_set"]
        st.caption(f"生成於 {plan_set.generated_at}")

        # ── 三個一起跑模擬 ────────────────────────────────────────
        if st.button("🎯 三個計劃全部跑模擬（同時比較）",
                     use_container_width=True, type="primary"):
            from research_db import init_db as _init_db
            from strategy_executor import start_execution as _start_exec
            _init_db(_RESEARCH_DB)
            exec_map = {}
            for pt in ("aggressive", "balanced", "conservative"):
                eid = _start_exec(plan_set, pt, _RESEARCH_DB)
                exec_map[pt] = eid
            st.session_state["_exec_map"] = exec_map
            _start_executor()

            # ── 三套全跑：全部標為影子追蹤，待使用者事後點選實際路徑 ──
            _track_record = DailyTrackRecord(
                date=date.today(),
                starting_capital=current_value,
                chosen_plan_type="",   # 尚未選擇
                results=[
                    PlanResult(
                        plan_type=pt,
                        pnl=None, pnl_pct=None, closing_capital=None,
                        is_actual=False,
                    )
                    for pt in ("aggressive", "balanced", "conservative")
                ],
            )
            try:
                save_day_record(_track_record)
            except Exception:
                pass

            st.success("✅ 三套計劃已同時啟動模擬！每 10 分鐘更新。")
            st.rerun()

        plan_cols = st.columns(3)
        plans = [
            ("🔥 衝刺版", plan_set.aggressive,   "#3a1a1a"),
            ("⚖️ 均衡版", plan_set.balanced,     "#1a1a3a"),
            ("🛡️ 保守版", plan_set.conservative, "#1a3a2a"),
        ]
        for col, (label, plan, bg) in zip(plan_cols, plans):
            with col:
                st.markdown(
                    f"<div style='background:{bg};padding:14px;border-radius:10px;'>"
                    f"<div style='font-size:16px;font-weight:bold;margin-bottom:4px'>{label}</div>"
                    f"<div style='color:#ccc;font-size:13px'>{plan.description}</div>"
                    f"<div style='color:#aaa;font-size:12px;margin-top:6px'>"
                    f"動用資金 {plan.capital_deployed_pct*100:.0f}%　"
                    f"預期報酬 {plan.expected_return_pct:+.1f}%</div>"
                    f"<div style='color:#f66;font-size:11px;margin-top:4px'>⚠️ {plan.risk_note}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                # 整體論述
                if plan.thesis:
                    with st.expander("📋 投資論述", expanded=True):
                        st.markdown(plan.thesis)
                        if plan.macro_context:
                            st.caption(f"總體環境：{plan.macro_context}")

                # 計算本套計劃總成本
                _plan_total = 0
                _pick_costs = {}
                _BUY_ACTIONS = {"buy", "open", "add"}
                for pick in plan.picks:
                    _, _snap = fetch_snapshot(pick.code)
                    _px = float(_snap.close) if _snap else 0.0
                    if pick.action in _BUY_ACTIONS:
                        _cost = _px * 1000 * pick.quantity if pick.quantity else _px * (pick.shares or 0)
                        _pick_costs[pick.code] = (_px, _cost)
                        _plan_total += _cost

                st.markdown("**選股：**")
                st.info(
                    f"📌 **這是三套方案之一，選其中一套執行。**\n\n"
                    f"執行後系統會**同時買進**此套計劃內所有股票（共 {len(plan.picks)} 支）。\n\n"
                    f"💰 **本套預估總花費：{_plan_total:,.0f} 元**"
                    if _plan_total > 0 else
                    f"📌 **這是三套方案之一，選其中一套執行。**\n執行後系統會**同時買進**此套計劃內所有股票。"
                )
                for pick in plan.picks:
                    action_icon = {"buy": "🟢", "open": "🟢", "add": "🔵", "reduce": "🟡", "close": "🔴", "switch": "🔄"}.get(pick.action, "⬜")
                    _px, _cost = _pick_costs.get(pick.code, (0.0, 0.0))
                    price_text = f"現價 {_px:.2f}" if _px else "無報價"
                    cost_text  = f"需 {_cost:,.0f} 元" if _cost else ""

                    with st.expander(
                        f"{action_icon} {pick.code} {pick.name}　"
                        f"{pick.quantity}張　{pick.hold_days}天　"
                        f"{pick.expected_return_pct:+.1f}%　信心{pick.confidence}/10　"
                        f"｜{price_text}　{cost_text}"
                    ):
                        st.markdown(f"**核心理由：** {pick.reason}")
                        if pick.key_catalysts:
                            st.markdown("**催化劑：**")
                            for cat in pick.key_catalysts:
                                st.markdown(f"- {cat}")
                        if pick.entry_logic:
                            st.markdown(f"**進場：** {pick.entry_logic}")
                        if pick.exit_logic:
                            st.markdown(f"**出場：** {pick.exit_logic}")
                st.markdown("")
                if st.button(f"▶️ 單獨執行此計劃",
                             key=f"exec_{plan.plan_type}", use_container_width=True):
                    from research_db import init_db as _init_db
                    from strategy_executor import start_execution as _start_exec
                    _init_db(_RESEARCH_DB)
                    exec_id = _start_exec(plan_set, plan.plan_type, _RESEARCH_DB)
                    st.session_state["_exec_map"] = {plan.plan_type: exec_id}
                    _start_executor()

                    # ── 建立今日追蹤記錄（實際路徑 + 影子追蹤）──
                    _all_types = ("aggressive", "balanced", "conservative")
                    _track_record = DailyTrackRecord(
                        date=date.today(),
                        starting_capital=current_value,
                        chosen_plan_type=plan.plan_type,
                        results=[
                            PlanResult(
                                plan_type=pt,
                                pnl=None,
                                pnl_pct=None,
                                closing_capital=None,
                                is_actual=(pt == plan.plan_type),
                            )
                            for pt in _all_types
                        ],
                    )
                    try:
                        save_day_record(_track_record)
                        st.toast("📊 今日追蹤記錄已建立（實際路徑 + 影子追蹤）", icon="📊")
                    except Exception:
                        pass

                    # ── 記錄模擬持倉（抓當前股價作為買入價）──
                    try:
                        _api = get_api()
                        _nm  = get_name_map()
                        _sim_positions: list[SimPosition] = []
                        for _pick in plan.picks:
                            _entry_price = _pick.entry_price if hasattr(_pick, "entry_price") and _pick.entry_price else 0.0
                            try:
                                _contract = _api.Contracts.Stocks.get(_pick.code)
                                if _contract:
                                    import threading as _th
                                    _snap_result: list = []
                                    def _snap_fetch():
                                        try:
                                            _snap_result.extend(_api.snapshots([_contract]))
                                        except Exception:
                                            pass
                                    _t = _th.Thread(target=_snap_fetch, daemon=True)
                                    _t.start()
                                    _t.join(timeout=6)
                                    if _snap_result:
                                        _entry_price = float(_snap_result[0].close)
                            except Exception:
                                pass
                            _sim_positions.append(SimPosition(
                                code=_pick.code,
                                name=_nm.get(_pick.code, _pick.name),
                                entry_price=_entry_price,
                                shares=getattr(_pick, "shares", 0),
                                quantity=getattr(_pick, "quantity", 0),
                                is_fractional=getattr(_pick, "is_fractional", False),
                                plan_type=plan.plan_type,
                                entry_date=date.today().isoformat(),
                            ))
                        save_sim_positions(_sim_positions)
                        print(f"[模擬持倉] 已記錄 {len(_sim_positions)} 支股票買入價", flush=True)
                        st.toast(f"📌 已記錄 {len(_sim_positions)} 支模擬持倉", icon="📌")
                    except Exception as _e:
                        print(f"[模擬持倉] 記錄失敗：{_e}", flush=True)

                    st.success(f"✅ 已啟動！ID: {exec_id}")
                    st.rerun()

    # ── 歷史計劃 ──────────────────────────────────────────────────
    _sim_plan_dir = "data/sim_plans"
    _saved_plans  = list_sim_plans(plan_dir=_sim_plan_dir)
    if _saved_plans:
        with st.expander(f"📂 歷史計劃（共 {len(_saved_plans)} 份）", expanded=False):
            st.caption("可載入舊計劃繼續檢視，或以其收盤資金為基準重新生成下一天的三套計劃。")
            for entry in _saved_plans:
                col_info, col_preview, col_load, col_regen, col_del = st.columns([3, 1, 1, 2, 1])
                col_info.markdown(
                    f"**{entry['day_label']}**　📅 {entry['plan_date']}"
                )
                preview_key = f"_preview_{entry['filename']}"
                if col_preview.button("👁", key=f"prev_{entry['filename']}", use_container_width=True, help="預覽策略內容"):
                    if st.session_state.get(preview_key):
                        st.session_state.pop(preview_key)
                    else:
                        prev_plan = load_sim_plan(entry["filename"], plan_dir=_sim_plan_dir)
                        if prev_plan:
                            st.session_state[preview_key] = prev_plan
                if col_load.button("載入", key=f"load_{entry['filename']}", use_container_width=True):
                    loaded = load_sim_plan(entry["filename"], plan_dir=_sim_plan_dir)
                    if loaded:
                        st.session_state["_plan_set"] = loaded
                        st.toast(f"已載入 {entry['day_label']} 計劃（維持不變）", icon="📂")
                        st.rerun()
                    else:
                        st.error("載入失敗")
                if col_del.button("🗑️", key=f"del_plan_{entry['filename']}", use_container_width=True, help="刪除此計劃"):
                    try:
                        delete_sim_plan(entry["filename"], plan_dir=_sim_plan_dir)
                        st.toast(f"已刪除 {entry['day_label']}", icon="🗑️")
                        st.rerun()
                    except Exception as e:
                        st.error(f"刪除失敗：{e}")

                # 預覽展開
                if st.session_state.get(preview_key):
                    _prev = st.session_state[preview_key]
                    for _pt, _label in [("aggressive", "🔴 積極版"), ("balanced", "⚖️ 均衡版"), ("conservative", "🟢 保守版")]:
                        _plan = getattr(_prev, _pt, None)
                        if _plan is None:
                            continue
                        with st.expander(f"{_label}　{_plan.description[:30]}...", expanded=False):
                            for _pick in _plan.picks:
                                st.markdown(
                                    f"**{_pick.code} {_pick.name}**　"
                                    f"預期 +{_pick.expected_return_pct:.0f}%　"
                                    f"持有 {_pick.hold_days} 天　"
                                    f"信心 {_pick.confidence}/10"
                                )
                                st.caption(_pick.reason)
                    st.divider()

                # 重新生成：讓使用者輸入當天收盤後的資金，以此作為下一天的起始資金
                regen_key = f"regen_cap_{entry['filename']}"
                with col_regen:
                    if st.button(
                        "以此為基準重新生成 ➜",
                        key=f"regen_{entry['filename']}",
                        use_container_width=True,
                        type="primary",
                    ):
                        st.session_state["_regen_from"] = entry["filename"]
                        st.rerun()

            # 如果使用者按了「以此為基準重新生成」，顯示資金輸入框
            if "_regen_from" in st.session_state:
                regen_fn = st.session_state["_regen_from"]
                regen_label = regen_fn.split("_")[0]  # e.g. "day1"
                st.markdown(f"---\n**以 {regen_label} 為基準，設定新的起始資金：**")
                prev_cap = float(goal.initial_capital)
                new_cap = st.number_input(
                    "收盤後資金（元）", min_value=1000.0,
                    value=prev_cap, step=1000.0, format="%.0f",
                    key="regen_capital_input",
                )
                col_ok, col_cancel = st.columns(2)
                if col_ok.button("✅ 確認，重新生成", use_container_width=True, type="primary"):
                    regen_goal = StrategyGoal(
                        initial_capital=new_cap,
                        target_multiplier=goal.target_multiplier,
                        start_date=goal.start_date,
                        end_date=goal.end_date,
                        approach=goal.approach,
                        stop_loss_pct=goal.stop_loss_pct,
                        max_positions=goal.max_positions,
                        allow_fractional=goal.allow_fractional,
                        allow_day_trade=goal.allow_day_trade,
                    )
                    print(f"[策略重新生成] 開始｜new_cap={new_cap:,.0f}")
                    with st.spinner("AI 分析中，約需 30–60 秒..."):
                        _cands = _build_candidates()
                        print(f"[策略重新生成] 候選股 {len(_cands)} 支：{[c['code'] for c in _cands]}")
                        print("[策略重新生成] 呼叫 Claude API...")
                        plan_set = generate_strategy_plans(regen_goal, new_cap, _cands)
                    if plan_set:
                        print(f"[策略重新生成] ✅ 成功")
                        st.session_state["_plan_set"] = plan_set
                        try:
                            sp = save_sim_plan(plan_set)
                            st.toast(f"新計劃已存檔：{sp.name}", icon="💾")
                        except Exception:
                            pass
                        st.session_state.pop("_regen_from", None)
                        st.rerun()
                    else:
                        st.error("生成失敗，請重試")
                if col_cancel.button("取消", use_container_width=True):
                    st.session_state.pop("_regen_from", None)
                    st.rerun()

    # ── AI 策略追蹤記錄（DailyTrackRecord）──────────────────────────
    _track_records = list_day_records()
    if _track_records:
        with st.expander(f"📊 AI 策略追蹤記錄（共 {len(_track_records)} 天）", expanded=False):
            st.caption("每日選擇計劃後自動記錄，可刪除錯誤或測試資料。")
            for rec in _track_records[:30]:  # 最多顯示 30 筆
                actual = next((r for r in rec.results if r.is_actual), None)
                pnl_str = f"{actual.pnl:+,.0f} 元" if (actual and actual.pnl is not None) else "待結算"
                col_info, col_del = st.columns([5, 1])
                col_info.markdown(
                    f"**{rec.date}**　選 `{rec.chosen_plan_type}`　損益 {pnl_str}"
                )
                if col_del.button("🗑️", key=f"del_track_{rec.date}", use_container_width=True, help="刪除此日追蹤記錄"):
                    try:
                        delete_day_record(rec.date)
                        st.toast(f"已刪除 {rec.date} 追蹤記錄", icon="🗑️")
                        st.rerun()
                    except Exception as e:
                        st.error(f"刪除失敗：{e}")

    st.divider()

    # ── 今日紀錄 ──────────────────────────────────────────────────
    st.markdown("#### 📝 今日紀錄")

    # 自動結算按鈕
    _sim_pos_path = "data/sim_positions.json"
    _sim_positions = load_sim_positions(_sim_pos_path)
    if _sim_positions:
        st.caption(f"📌 有 {len(_sim_positions)} 支模擬持倉（{_sim_positions[0].plan_type}，買入日：{_sim_positions[0].entry_date}）")
        if st.button("📊 自動結算（抓收盤價計算損益）", use_container_width=True):
            with st.spinner("抓收盤價中..."):
                _api = get_api()
                _codes = [p.code for p in _sim_positions]
                _price_map = fetch_closing_prices(_api, _codes)
            if not _price_map:
                st.warning("收盤價抓取失敗或超時，請稍後再試")
            else:
                _settlement = settle_positions(_sim_positions, _price_map)
                st.session_state["_auto_pnl"] = _settlement["total_pnl"]
                st.success(f"結算完成！總損益：{_settlement['total_pnl']:+,.0f} 元（{_settlement['total_pnl_pct']:+.2f}%）")
                for _s in _settlement["per_stock"]:
                    _icon = "🟢" if _s["pnl"] >= 0 else "🔴"
                    st.caption(f"{_icon} {_s['code']} {_s['name']}　買入 {_s['entry_price']:.1f} → 收盤 {_s['closing_price']:.1f}　損益 {_s['pnl']:+,.0f} 元")
                # 推播到 Telegram
                try:
                    notify_settlement(
                        plan_type=_sim_positions[0].plan_type,
                        settlement=_settlement,
                        entry_date=_sim_positions[0].entry_date,
                    )
                    st.toast("📨 已推播到 Telegram", icon="📨")
                except Exception as _ne:
                    print(f"[Telegram] 推播失敗：{_ne}", flush=True)
                st.rerun()
    _auto_pnl = st.session_state.pop("_auto_pnl", None)

    with st.form("daily_form"):
        col_p, col_t = st.columns(2)
        _pnl_default = float(_auto_pnl if _auto_pnl is not None else (today_entry.pnl if today_entry else 0.0))
        pnl_input    = col_p.number_input("今日損益（元）", value=_pnl_default, step=100.0, format="%.0f")
        trades_input = col_t.number_input("今日交易次數", min_value=0, value=int(today_entry.trades_count if today_entry else 0))
        review_input = st.text_area("今日檢討（手動填寫，或點下方讓 AI 生成）",
                                    value=today_entry.review if today_entry else "", height=80)
        plan_input   = st.text_area("明日計畫",
                                    value=today_entry.tomorrow_plan if today_entry else "", height=80)
        submitted = st.form_submit_button("💾 儲存今日紀錄", use_container_width=True)

    if submitted:
        entry = DailyEntry(
            date=today,
            pnl=pnl_input,
            trades_count=int(trades_input),
            review=review_input,
            tomorrow_plan=plan_input,
            target_met=check_daily_target(pnl_input, daily_target),
        )
        # 如果今天已有記錄則重寫所有 entries
        all_entries = [e for e in entries if e.date != today] + [entry]
        Path(_ENTRIES_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(_ENTRIES_PATH).write_text("")
        for e in sorted(all_entries, key=lambda x: x.date):
            save_daily_entry(e, _ENTRIES_PATH)
        st.success("今日紀錄已儲存")
        st.rerun()

    # AI 生成檢討按鈕（在 form 外）
    if st.button("🤖 AI 生成今日檢討與明日計畫", use_container_width=True):
        yesterday_plan = ""
        if entries:
            prev = [e for e in entries if e.date < today]
            if prev:
                yesterday_plan = sorted(prev, key=lambda x: x.date)[-1].tomorrow_plan
        with st.spinner("AI 生成中..."):
            result = generate_daily_review(
                goal=goal,
                today_pnl=today_entry.pnl if today_entry else 0.0,
                daily_target=daily_target,
                trades=[],
                yesterday_plan=yesterday_plan,
            )
        st.info(f"**檢討：** {result['review']}")
        st.info(f"**明日計畫：** {result['tomorrow_plan']}")

    # ── 歷史紀錄 ──────────────────────────────────────────────────
    if entries:
        st.divider()
        st.markdown("#### 📅 歷史紀錄")
        for e in sorted(entries, key=lambda x: x.date, reverse=True)[:14]:
            icon = "✅" if e.target_met else "❌"
            pnl_color = "#ef5350" if e.pnl > 0 else "#26a69a" if e.pnl < 0 else "#aaa"
            with st.expander(f"{icon} {e.date}　損益 {e.pnl:+,.0f} 元　{e.trades_count} 筆交易"):
                st.markdown(f"**檢討：** {e.review or '（未填）'}")
                st.markdown(f"**明日計畫：** {e.tomorrow_plan or '（未填）'}")


def tab_ai():
    log_tab, chat_tab, tracker_tab, dt_tab = st.tabs(
        ["📋 決策記錄", "💬 AI 顧問對話", "📈 策略追蹤", "⚡ 當沖策略"]
    )

    # ── 決策記錄 ──────────────────────────────────────────────────
    with log_tab:
        c1, c2, c3 = st.columns([2, 2, 1])
        c1.info(f"📰 新聞：`{config.NEWS_MODEL}`")
        c2.info(f"🧠 決策：`{config.DECISION_MODEL}`")
        if c3.button("🔄 刷新"):
            st.rerun()

        log_path = Path("data/ai_log.jsonl")
        if not log_path.exists():
            st.info("尚無 AI 記錄")
            st.code("python3 simulate.py\npython3 auto_trader.py", language="bash")
        else:
            entries = []
            with log_path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
            if not entries:
                st.info("記錄檔為空")
            else:
                entries = list(reversed(entries))
                latest = entries[0]
                d = latest["decision"]
                action = d["action"]
                bg = {"buy": "#1a3a2a", "sell": "#3a1a1a"}.get(action, "#1a1a2a")
                label = {"buy": "🟢 買進", "sell": "🔴 賣出", "hold": "⬜ 持有"}.get(action, action)
                st.markdown(
                    f"<div style='background:{bg};padding:20px;border-radius:10px;margin-bottom:16px'>"
                    f"<div style='font-size:22px;font-weight:bold'>{label}"
                    f"{'　' + d.get('stock_code','') if d.get('stock_code') else ''}</div>"
                    f"<div style='color:#ccc;margin-top:8px'>{d.get('reason','')}</div>"
                    f"<div style='color:#666;font-size:12px;margin-top:6px'>"
                    f"信心 {d.get('confidence',0)}/10　新聞 {latest.get('news_sentiment','')} "
                    f"{latest.get('news_score','-')}/10　{latest['timestamp'][11:19]}</div></div>",
                    unsafe_allow_html=True,
                )
                st.divider()
                for e in entries[:20]:
                    dec = e["decision"]
                    act = dec["action"]
                    icon = {"buy": "🟢", "sell": "🔴", "hold": "⬜"}.get(act, "⬜")
                    with st.expander(
                        f"{icon} {e['timestamp'][11:19]}　{act.upper()}"
                        f"{'  ' + dec.get('stock_code','') if dec.get('stock_code') else ''}"
                        f"　信心 {dec.get('confidence',0)}/10"
                    ):
                        st.markdown(f"**理由：** {dec.get('reason','')}")
                        col1, col2 = st.columns(2)
                        col1.metric("新聞情緒", f"{e.get('news_sentiment','')} {e.get('news_score','-')}/10")
                        col2.metric("模型", e.get("model_used", "-"))

    # ── AI 顧問對話 ────────────────────────────────────────────────
    with chat_tab:
        positions = fetch_positions()
        ai_log = load_latest_ai_log()

        # 清除對話
        col_title, col_clear = st.columns([5, 1])
        col_title.markdown("#### 💬 與 AI 投資顧問對話")
        if col_clear.button("🗑️ 清除"):
            st.session_state.chat_messages = []
            st.rerun()

        st.caption("模型：`claude-sonnet-4-5`　（Anthropic API）")

        # 快速問題按鈕
        st.markdown("**快速提問：**")
        quick = get_quick_prompts()
        cols = st.columns(3)
        for i, q in enumerate(quick):
            if cols[i % 3].button(q["label"], key=f"quick_{i}", use_container_width=True):
                with st.spinner("AI 思考中..."):
                    _send_chat(q["prompt"], positions, ai_log)
                st.rerun()

        st.divider()

        # 對話記錄
        chat_msgs = st.session_state.chat_messages
        display_msgs = [m for m in chat_msgs if m.role != "system"]
        if not display_msgs:
            st.info("還沒有對話。點擊上方快速提問，或在下方輸入你的問題。")
        else:
            for msg in display_msgs:
                with st.chat_message(msg.role):
                    st.markdown(msg.content)
                    if msg.timestamp:
                        st.caption(msg.timestamp)

        # 使用者輸入框
        user_input = st.chat_input("輸入你的問題（例：台積電現在適合買嗎？）")
        if user_input:
            with st.spinner("AI 思考中..."):
                _send_chat(user_input, positions, ai_log)
            st.rerun()

        # ── 總結並執行 ────────────────────────────────────────────
        if display_msgs:
            st.divider()
            st.markdown("#### 📋 總結策略並執行")
            st.caption("讓 AI 從對話中提取最終決策，並驗證交易規範後執行")

            if st.button("🎯 從對話總結策略", use_container_width=True):
                with st.spinner("AI 總結中..."):
                    decision = summarize_chat_to_strategy(
                        [m for m in chat_msgs if m.role != "system"]
                    )
                st.session_state["_pending_decision"] = decision
                st.rerun()

            if "_pending_decision" in st.session_state:
                decision = st.session_state["_pending_decision"]
                if st.session_state.rules is None:
                    st.session_state.rules = load_rules()
                rules = st.session_state.rules

                action = decision["action"]
                action_label = {"buy": "🟢 買進", "sell": "🔴 賣出", "hold": "⬜ 持有"}.get(action, action)
                bg = {"buy": "#1a3a2a", "sell": "#3a1a1a"}.get(action, "#1e1e2e")
                st.markdown(
                    f"<div style='background:{bg};padding:14px;border-radius:8px'>"
                    f"<b>{action_label} {decision.get('stock_code','')}</b>　{decision.get('quantity',1)} 張<br>"
                    f"<span style='color:#ccc'>{decision.get('reason','')}</span><br>"
                    f"<span style='color:#888;font-size:12px'>信心 {decision.get('confidence',0)}/10</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                # 即時取得現價
                current_price = None
                if action == "buy" and decision.get("stock_code"):
                    try:
                        _, snap = fetch_snapshot(decision["stock_code"])
                        current_price = snap.close if snap else None
                    except Exception:
                        pass

                validation = validate_decision(
                    decision, rules, config.BUDGET,
                    current_price,
                    buys_today=st.session_state.buys_today,
                    sells_today=st.session_state.sells_today,
                )

                if validation.allowed:
                    qty = validation.adjusted_quantity or decision.get("quantity", 1)
                    if validation.adjusted_quantity:
                        st.warning(f"⚠️ 規範自動調整：{validation.reason}")
                    else:
                        st.success(f"✅ 通過規範驗證：{validation.reason}")

                    if st.button(f"🚀 確認執行（{action_label} {qty} 張）", type="primary"):
                        if action == "buy":
                            st.session_state.buys_today += 1
                        elif action == "sell":
                            st.session_state.sells_today += 1
                        del st.session_state["_pending_decision"]
                        st.success("委託已送出（模擬盤）")
                        st.rerun()
                else:
                    st.error(f"❌ 規範驗證不通過：{validation.reason}")
                    if st.button("取消"):
                        del st.session_state["_pending_decision"]
                        st.rerun()

    # ── 策略追蹤 ───────────────────────────────────────────────────
    with tracker_tab:
        _render_strategy_tracker()

    # ── 當沖策略 ───────────────────────────────────────────────────
    with dt_tab:
        st.caption("掃描全市場，依技術評分選出今日當沖候選股，並執行 AI 深度分析。")
        col_btn, col_refresh = st.columns([2, 1])
        if col_btn.button("⚡ 產生當沖報告", type="primary"):
            with st.spinner("掃描中，約需 10–30 秒..."):
                try:
                    from daytrading_report import build_daytrading_report
                    report = build_daytrading_report(api=None)
                    st.session_state["_dt_report"] = report
                except Exception as e:
                    st.session_state["_dt_report"] = f"⚠️ 產生失敗：{e}"
            st.rerun()
        if "_dt_report" in st.session_state:
            st.markdown(
                st.session_state["_dt_report"]
                    .replace("<b>", "**").replace("</b>", "**")
                    .replace("<i>", "*").replace("</i>", "*")
                    .replace("<br>", "\n"),
                unsafe_allow_html=False,
            )

        st.divider()
        st.markdown("#### 📊 歷史複盤（近 14 日）")
        try:
            from daytrading_db import DaytradingDB
            db = DaytradingDB()
            stats = db.win_rate_summary(days=30)
            history = db.recent_history(days=14)

            if stats["total"] > 0:
                wr = (stats["win_rate"] or 0) * 100
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("近 30 日預測", f"{stats['total']} 筆")
                c2.metric("達標", stats["wins"])
                c3.metric("停損", stats["losses"])
                c4.metric("勝率", f"{wr:.1f}%")

            if history:
                import pandas as pd
                rows = []
                for r in history:
                    outcome_label = {
                        "hit_target": "✅ 達目標",
                        "hit_stop":   "❌ 觸停損",
                        "neutral":    "⬜ 未觸發",
                        "untestable": "⚠️ 資料不足",
                    }.get(r["outcome"], r["outcome"])
                    rows.append({
                        "日期":   r["date"],
                        "代號":   r["code"],
                        "名稱":   r["name"],
                        "評分":   r["dt_score"],
                        "目標價": r["target_price"],
                        "停損價": r["stop_loss"],
                        "最高":   r["daily_high"],
                        "最低":   r["daily_low"],
                        "收盤":   r["daily_close"],
                        "結果":   outcome_label,
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.info("尚無複盤記錄，收盤後自動回填。")
        except Exception as e:
            st.caption(f"複盤資料暫時無法讀取：{e}")


# ── Tab 7: 交易規範 ───────────────────────────────────────────────────────────

def tab_rules():
    if st.session_state.rules is None:
        st.session_state.rules = load_rules()
    rules: TradingRules = st.session_state.rules

    st.subheader("⚙️ 交易規範設定")
    st.caption("這些規範會在 AI 總結策略時自動驗證，避免超出風險承受範圍")

    changed = False
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**📊 倉位管理**")
        new_max_pct = st.slider(
            "單筆最大倉位（% 本金）",
            min_value=10, max_value=100, value=int(rules.max_position_pct * 100), step=5,
            help="每次買入最多使用本金的幾 %",
        )
        new_trades = st.number_input(
            "每天最多買賣次數",
            min_value=1, max_value=10, value=rules.max_trades_per_day,
            help="買和賣分開計算，各最多幾次",
        )
        today_col1, today_col2 = st.columns(2)
        today_col1.metric("今日已買", st.session_state.buys_today)
        today_col2.metric("今日已賣", st.session_state.sells_today)
        if st.button("🔄 重置今日計數"):
            st.session_state.buys_today = 0
            st.session_state.sells_today = 0
            st.rerun()

    with col2:
        st.markdown("**🛡️ 風險控管**")
        new_stop = st.slider(
            "停損線（跌幾 % 認賠）",
            min_value=1.0, max_value=20.0, value=rules.stop_loss_pct, step=0.5,
            help="持股跌超過此 % 自動停損",
        )
        new_tp = st.slider(
            "停利線（漲幾 % 獲利了結）",
            min_value=3.0, max_value=50.0, value=rules.take_profit_pct, step=1.0,
            help="持股漲超過此 % 自動停利",
        )
        new_conf = st.slider(
            "最低 AI 信心分數（才執行）",
            min_value=0, max_value=10, value=rules.min_confidence,
            help="AI 信心分數低於此值的決策不執行",
        )

    st.divider()

    # 規範預覽
    st.markdown("**📋 目前規範摘要**")
    budget = config.BUDGET
    max_single = budget * (new_max_pct / 100)
    st.markdown(
        f"- 每筆最多投入：**${max_single:,.0f}**（本金 ${budget:,.0f} × {new_max_pct}%）\n"
        f"- 每天買入：最多 **{new_trades} 次**，賣出：最多 **{new_trades} 次**\n"
        f"- 停損：跌超過 **{new_stop:.1f}%** 認賠出場\n"
        f"- 停利：漲超過 **{new_tp:.1f}%** 獲利了結\n"
        f"- AI 最低信心門檻：**{new_conf}/10**（低於此不執行）"
    )

    st.divider()
    if st.button("💾 儲存規範", type="primary"):
        new_rules = TradingRules(
            max_trades_per_day=int(new_trades),
            max_position_pct=new_max_pct / 100,
            stop_loss_pct=new_stop,
            take_profit_pct=new_tp,
            min_confidence=new_conf,
        )
        save_rules(new_rules)
        st.session_state.rules = new_rules
        st.success("✅ 規範已儲存！")

    # ── 當沖參數 ──────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("⚡ 當沖參數設定")
    st.caption("調整後點「儲存」，下次 main.py 啟動時生效；盤中不會即時套用到已啟動的監控。")

    if "dt_cfg" not in st.session_state:
        st.session_state.dt_cfg = load_daytrading_config()
    dt_cfg: DaytradingConfig = st.session_state.dt_cfg

    dc1, dc2 = st.columns(2)
    with dc1:
        st.markdown("**💰 資金 / 執行**")
        new_budget = st.number_input(
            "每支股票預算（元）",
            min_value=10_000, max_value=500_000,
            value=int(dt_cfg.budget_per_stock), step=5_000,
            help="當沖每支股票最多使用多少元",
        )
        new_force_close = st.text_input(
            "強制平倉時間（HH:MM）",
            value=dt_cfg.force_close_time,
            help="到達此時間無條件賣出所有當沖倉位，避免產生交割義務",
        )
        new_manual = st.checkbox(
            "買入前需 Telegram 手動確認",
            value=dt_cfg.require_manual_confirm,
            help="關閉後系統自動下單，請確認帳戶設定無誤再關閉",
        )

    with dc2:
        st.markdown("**🎯 停損 / 停利**")
        new_dt_stop = st.slider(
            "停損線（跌幾 % 認賠）",
            min_value=0.5, max_value=10.0, value=float(dt_cfg.stop_loss_pct), step=0.5,
            help="買入後跌超過此 % 立即賣出",
        )
        new_dt_tp = st.slider(
            "天花板停利（漲幾 % 出場）",
            min_value=1.0, max_value=15.0, value=float(dt_cfg.take_profit_pct), step=0.5,
            help="漲幅達此 % 立即賣出（漲停前出場）",
        )
        st.markdown("**📈 移動停損**")
        new_trailing_start = st.slider(
            "啟動門檻（漲到幾 % 開始跟蹤）",
            min_value=1.0, max_value=8.0, value=float(dt_cfg.trailing_start_pct), step=0.5,
            help="漲幅達此值後，停損點開始跟著最高點移動",
        )
        new_trailing_gap = st.slider(
            "間距（停損點在最高點下方幾 %）",
            min_value=0.5, max_value=5.0, value=float(dt_cfg.trailing_gap_pct), step=0.5,
            help="例：設 2%，最高點 +5% → 停損跟在 +3%",
        )

    st.markdown(
        f"**預覽：** 漲到 **+{new_trailing_start:.1f}%** 開始移動停損，"
        f"停損永遠在最高點下方 **{new_trailing_gap:.1f}%**　｜　"
        f"固定停損 **-{new_dt_stop:.1f}%**　｜　天花板 **+{new_dt_tp:.1f}%**"
    )

    if st.button("💾 儲存當沖參數", type="primary", key="save_dt_cfg"):
        new_cfg = DaytradingConfig(
            budget_per_stock=float(new_budget),
            stop_loss_pct=new_dt_stop,
            take_profit_pct=new_dt_tp,
            trailing_start_pct=new_trailing_start,
            trailing_gap_pct=new_trailing_gap,
            force_close_time=new_force_close.strip(),
            require_manual_confirm=new_manual,
        )
        save_daytrading_config(new_cfg)
        st.session_state.dt_cfg = new_cfg
        st.success("✅ 當沖參數已儲存！下次啟動 main.py 時生效。")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with st.sidebar:
        st.title("📈 台股交易系統")
        simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"
        if simulation:
            st.warning("模擬盤")
        else:
            st.success("真實帳戶")
        st.caption(f"更新：{datetime.now().strftime('%H:%M:%S')}")
        if st.button("立即更新"):
            st.cache_data.clear()
            st.rerun()

    t1, t2, t3, t4, t5, t6, t7 = st.tabs(
        ["🏠 總覽", "📊 市場", "💼 投資組合", "🛒 交易", "🔔 警示", "🤖 AI 決策", "⚙️ 規範"]
    )
    with t1: tab_dashboard()
    with t2: tab_market()
    with t3: tab_portfolio()
    with t4: tab_trade()
    with t5: tab_alerts()
    with t6: tab_ai()
    with t7: tab_rules()


if __name__ == "__main__":
    main()
