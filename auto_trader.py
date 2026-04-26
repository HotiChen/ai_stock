"""
自動交易排程器 — 獨立執行：python3 auto_trader.py

流程：
  09:05  → AI 決定買什麼
  每 5 分鐘 → AI 決定要不要賣
  13:25  → 強制平倉
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, time as dtime

import shioaji as sj
import shioaji.constant as sc
from dotenv import load_dotenv

import config
from ai_trader import WATCHLIST, log_decision, make_enriched_decision
from logic import add_indicators, normalize_kbars_df
from news_agent import get_news_context
from strategies import calc_position_pnl, trailing_stop_triggered
from trades import TradeAction, TradeRecord, save_trade

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("data/auto_trader.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(13, 30)
BUY_WINDOW_START = dtime(9, 5)
FORCE_CLOSE = dtime(*map(int, config.FORCE_CLOSE_TIME.split(":")))
CHECK_INTERVAL = 300  # 5 分鐘


def get_api():
    simulation = os.getenv("SHIOAJI_SIMULATION", "true").lower() == "true"
    api = sj.Shioaji(simulation=simulation)
    api.login(
        api_key=os.getenv("SHIOAJI_API_KEY"),
        secret_key=os.getenv("SHIOAJI_SECRET_KEY"),
        fetch_contract=True,
    )
    return api


def is_market_open() -> bool:
    now = datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE and datetime.now().weekday() < 5


def get_current_price(api, stock_code: str) -> float | None:
    contract = api.Contracts.Stocks.get(stock_code)
    if not contract:
        return None
    snapshots = api.snapshots([contract])
    return snapshots[0].close if snapshots else None


def get_indicators(api, stock_code: str) -> dict:
    from datetime import date, timedelta
    import pandas as pd
    contract = api.Contracts.Stocks.get(stock_code)
    if not contract:
        return {}
    end = date.today()
    start = end - timedelta(days=120)
    kbars = api.kbars(contract, str(start), str(end))
    df = pd.DataFrame({**kbars})
    if df.empty:
        return {}
    df = normalize_kbars_df(df)
    df = add_indicators(df)
    last = df.iloc[-1]
    return {
        "close": last["Close"],
        "MA5": round(last.get("MA5", 0) or 0, 2),
        "MA20": round(last.get("MA20", 0) or 0, 2),
        "MA60": round(last.get("MA60", 0) or 0, 2),
        "RSI": round(last.get("RSI", 50) or 50, 2),
        "MACD": round(last.get("MACD", 0) or 0, 4),
    }


def place_order(api, stock_code: str, action: sc.Action, quantity: int, price: float = 0) -> bool:
    try:
        contract = api.Contracts.Stocks.get(stock_code)
        if not contract:
            log.error(f"找不到合約：{stock_code}")
            return False
        price_type = sc.StockPriceType.MKT if price == 0 else sc.StockPriceType.LMT
        order = api.Order(
            price=price,
            quantity=quantity,
            action=action,
            price_type=price_type,
            order_type=sc.OrderType.ROD,
        )
        trade = api.place_order(contract, order)
        log.info(f"委託成功：{stock_code} {action} {quantity}張 → {trade.order.id}")
        return True
    except Exception as e:
        log.error(f"委託失敗：{e}")
        return False


def run():
    os.makedirs("data", exist_ok=True)
    log.info("自動交易排程器啟動")
    log.info(f"使用模型：新聞={config.NEWS_MODEL} 決策={config.DECISION_MODEL}")

    api = get_api()

    # 每日狀態（重置於每次啟動）
    state = {
        "bought_today": False,
        "sold_today": False,
        "position": None,      # {"stock_code", "quantity", "cost", "peak"}
        "cash": config.BUDGET,
    }

    while True:
        now = datetime.now()

        if not is_market_open():
            log.info("非交易時段，等待...")
            time.sleep(60)
            continue

        current_time = now.time()

        # ── 強制平倉 ──────────────────────────────────────────────
        if current_time >= FORCE_CLOSE and state["position"] and not state["sold_today"]:
            pos = state["position"]
            log.warning(f"強制平倉：{pos['stock_code']}")
            current_price = get_current_price(api, pos["stock_code"])
            ok = place_order(api, pos["stock_code"], sc.Action.Sell, pos["quantity"])
            if ok:
                pnl = calc_position_pnl(pos["cost"], current_price or pos["cost"], pos["quantity"] * 1000)
                save_trade(TradeRecord(
                    stock_code=pos["stock_code"],
                    action=TradeAction.SELL,
                    quantity=pos["quantity"] * 1000,
                    price=current_price or pos["cost"],
                    pnl=pnl["pnl_amount"],
                    trade_date=now.date(),
                ))
                state["sold_today"] = True
                state["position"] = None
            time.sleep(CHECK_INTERVAL)
            continue

        # ── 停損檢查 ──────────────────────────────────────────────
        if state["position"] and not state["sold_today"]:
            pos = state["position"]
            current_price = get_current_price(api, pos["stock_code"])
            if current_price:
                pos["peak"] = max(pos.get("peak", pos["cost"]), current_price)
                if trailing_stop_triggered(pos["cost"], current_price, pos["peak"], config.STOP_LOSS_PCT):
                    log.warning(f"停損觸發：{pos['stock_code']} 現價={current_price}")
                    ok = place_order(api, pos["stock_code"], sc.Action.Sell, pos["quantity"])
                    if ok:
                        pnl = calc_position_pnl(pos["cost"], current_price, pos["quantity"] * 1000)
                        save_trade(TradeRecord(
                            stock_code=pos["stock_code"],
                            action=TradeAction.SELL,
                            quantity=pos["quantity"] * 1000,
                            price=current_price,
                            pnl=pnl["pnl_amount"],
                            trade_date=now.date(),
                        ))
                        state["sold_today"] = True
                        state["position"] = None
                    time.sleep(CHECK_INTERVAL)
                    continue

        # ── AI 決策（每 5 分鐘）─────────────────────────────────
        log.info("取得大盤新聞情緒...")
        market_news = get_news_context()
        log.info(f"大盤情緒：{market_news['sentiment']} ({market_news['score']}/10)")

        # 選擇分析標的
        target = state["position"]["stock_code"] if state["position"] else WATCHLIST[0]
        from config import STOCK_NAMES
        target_name = STOCK_NAMES.get(target, target)

        indicators = get_indicators(api, target)
        if not indicators:
            log.warning("無法取得技術指標")
            time.sleep(CHECK_INTERVAL)
            continue

        log.info(f"抓取 {target_name}（{target}）個股新聞與基本面...")
        decision = make_enriched_decision(
            code=target,
            name=target_name,
            indicators=indicators,
            market_news=market_news,
            position=state["position"],
            cash=state["cash"],
            already_bought_today=state["bought_today"],
            already_sold_today=state["sold_today"],
        )

        log.info(f"AI 決策：{decision['action']} {decision.get('stock_code','')} 信心={decision['confidence']}/10")
        log.info(f"理由：{decision['reason']}")
        log_decision(decision, news)

        # ── 執行決策 ─────────────────────────────────────────────
        if decision["action"] == "buy" and not state["bought_today"] and current_time >= BUY_WINDOW_START:
            code = decision["stock_code"]
            qty = decision["quantity"]
            current_price = get_current_price(api, code)
            if current_price and current_price * qty * 1000 <= state["cash"]:
                ok = place_order(api, code, sc.Action.Buy, qty)
                if ok:
                    state["bought_today"] = True
                    state["cash"] -= current_price * qty * 1000
                    state["position"] = {
                        "stock_code": code,
                        "quantity": qty,
                        "cost": current_price,
                        "peak": current_price,
                    }
                    save_trade(TradeRecord(
                        stock_code=code,
                        action=TradeAction.BUY,
                        quantity=qty * 1000,
                        price=current_price,
                        pnl=0.0,
                        trade_date=now.date(),
                    ))
            else:
                log.warning(f"預算不足或無法取得價格：{code}")

        elif decision["action"] == "sell" and state["position"] and not state["sold_today"]:
            pos = state["position"]
            current_price = get_current_price(api, pos["stock_code"])
            ok = place_order(api, pos["stock_code"], sc.Action.Sell, pos["quantity"])
            if ok:
                pnl = calc_position_pnl(pos["cost"], current_price or pos["cost"], pos["quantity"] * 1000)
                save_trade(TradeRecord(
                    stock_code=pos["stock_code"],
                    action=TradeAction.SELL,
                    quantity=pos["quantity"] * 1000,
                    price=current_price or pos["cost"],
                    pnl=pnl["pnl_amount"],
                    trade_date=now.date(),
                ))
                state["sold_today"] = True
                state["cash"] += (current_price or pos["cost"]) * pos["quantity"] * 1000
                state["position"] = None

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
