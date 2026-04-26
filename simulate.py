"""
模擬交易環境 — 股市關閉時測試用

執行：python3 simulate.py

模擬內容：
- 假的股票價格（隨機遊走）
- 假的技術指標
- 假的新聞情緒
- 真實的 AI 決策邏輯（呼叫 Ollama）
- 真實的策略判斷（停損/停利）
"""
from __future__ import annotations

import json
import random
import time
from datetime import date, datetime

import numpy as np

import config
from ai_trader import log_decision, make_decision
from news_agent import analyze_sentiment
from strategies import calc_position_pnl, trailing_stop_triggered
from trades import TradeAction, TradeRecord, calc_total_pnl, calc_win_rate, load_trades, save_trade

# ── 假資料產生器 ──────────────────────────────────────────────────────────────

FAKE_STOCKS = {
    "2330": {"name": "台積電", "base_price": 850.0},
    "2317": {"name": "鴻海",   "base_price": 180.0},
    "2454": {"name": "聯發科", "base_price": 1200.0},
}

FAKE_HEADLINES = [
    ["台積電法說會傳樂觀展望，外資大幅加碼", "AI 伺服器需求強勁，半導體族群齊漲"],
    ["Fed 暗示升息，美股重挫，台股跟跌", "外資連續賣超，台股量縮走低"],
    ["台股量能平淡，主力觀望", "美股小漲，台股跟進震盪"],
    ["鴻海拿下 AI 大單，股價跳空漲停", "電子股輪動，傳產股補漲"],
    ["台積電傳客戶砍單，股價重挫", "全球景氣憂慮升溫，科技股承壓"],
]


def fake_price(base: float, step: int, volatility: float = 0.015) -> float:
    """隨機遊走產生假價格。"""
    drift = random.gauss(0, volatility)
    return round(base * (1 + drift * (step ** 0.3) / 10), 1)


def fake_indicators(price: float, step: int) -> dict:
    """產生合理的假技術指標。"""
    rsi = max(10, min(90, 50 + random.gauss(0, 15) + (step % 5 - 2) * 3))
    ma5 = price * random.uniform(0.98, 1.02)
    ma20 = price * random.uniform(0.96, 1.04)
    ma60 = price * random.uniform(0.93, 1.07)
    macd = random.gauss(0, 2)
    return {
        "close": price,
        "MA5":  round(ma5, 2),
        "MA20": round(ma20, 2),
        "MA60": round(ma60, 2),
        "RSI":  round(rsi, 1),
        "MACD": round(macd, 3),
    }


def fake_news(step: int) -> dict:
    """輪流使用不同情境的假新聞。"""
    headlines = FAKE_HEADLINES[step % len(FAKE_HEADLINES)]
    print(f"  📰 假新聞：{headlines[0]}")
    try:
        result = analyze_sentiment(headlines)
    except Exception:
        sentiments = ["positive", "negative", "neutral"]
        result = {
            "sentiment": sentiments[step % 3],
            "score": [7, 3, 5][step % 3],
            "reason": "模擬情緒",
        }
    result["headlines"] = headlines
    return result


# ── 模擬主迴圈 ────────────────────────────────────────────────────────────────

def run_simulation(rounds: int = 6, interval_sec: int = 5):
    print("\n" + "=" * 55)
    print("  🎮 台股 AI 交易模擬器（股市關閉測試模式）")
    print(f"  模型：新聞={config.NEWS_MODEL}  決策={config.DECISION_MODEL}")
    print("=" * 55 + "\n")

    # 初始狀態
    stock_code = random.choice(list(FAKE_STOCKS.keys()))
    stock_info = FAKE_STOCKS[stock_code]
    base_price = stock_info["base_price"]
    state = {
        "bought_today": False,
        "sold_today": False,
        "position": None,
        "cash": config.BUDGET,
    }

    print(f"  今日標的：{stock_info['name']}（{stock_code}）基準價 {base_price:.1f}\n")

    for step in range(rounds):
        now = datetime.now().strftime("%H:%M:%S")
        price = fake_price(base_price, step)
        print(f"{'─'*55}")
        print(f"  第 {step+1}/{rounds} 輪　{now}　現價：{price:.1f}")

        # 停損檢查
        if state["position"] and not state["sold_today"]:
            pos = state["position"]
            pos["peak"] = max(pos.get("peak", pos["cost"]), price)
            if trailing_stop_triggered(pos["cost"], price, pos["peak"], config.STOP_LOSS_PCT):
                pnl = calc_position_pnl(pos["cost"], price, pos["quantity"] * 1000)
                print(f"  ⚠️  停損觸發！現價={price:.1f} 成本={pos['cost']:.1f}")
                print(f"      損益：{pnl['pnl_amount']:+.0f} 台幣 ({pnl['pnl_pct']:+.1f}%)")
                _record_sell(pos, price, pnl["pnl_amount"])
                state["sold_today"] = True
                state["cash"] += price * pos["quantity"] * 1000
                state["position"] = None
                time.sleep(interval_sec)
                continue

        # 取得假指標 + 假新聞
        indicators = fake_indicators(price, step)
        news = fake_news(step)
        print(f"  📊 RSI={indicators['RSI']:.0f}  MACD={indicators['MACD']:.2f}  "
              f"新聞={news['sentiment']}({news.get('score',5)}/10)")

        # AI 決策
        print(f"  🤖 詢問 {config.DECISION_MODEL}...")
        decision = make_decision(
            indicators=indicators,
            news=news,
            position=state["position"],
            cash=state["cash"],
            already_bought_today=state["bought_today"],
            already_sold_today=state["sold_today"],
        )
        log_decision(decision, news)

        action_icon = {"buy": "🟢 BUY", "sell": "🔴 SELL", "hold": "⬜ HOLD"}
        print(f"  → {action_icon[decision['action']]}  "
              f"股票={decision['stock_code'] or '-'}  "
              f"信心={decision['confidence']}/10")
        print(f"  理由：{decision['reason'][:60]}")

        # 執行模擬決策
        if decision["action"] == "buy" and not state["bought_today"]:
            qty = max(1, decision["quantity"])
            cost = price * qty * 1000
            if cost <= state["cash"]:
                state["bought_today"] = True
                state["cash"] -= cost
                state["position"] = {
                    "stock_code": decision["stock_code"] or stock_code,
                    "quantity": qty,
                    "cost": price,
                    "peak": price,
                }
                save_trade(TradeRecord(
                    stock_code=state["position"]["stock_code"],
                    action=TradeAction.BUY,
                    quantity=qty * 1000,
                    price=price,
                    pnl=0.0,
                    trade_date=date.today(),
                ))
                print(f"  ✅ 模擬買入 {qty} 張，花費 {cost:,.0f} 台幣，剩餘 {state['cash']:,.0f}")
            else:
                print(f"  ❌ 預算不足（需要 {cost:,.0f}，剩餘 {state['cash']:,.0f}）")

        elif decision["action"] == "sell" and state["position"] and not state["sold_today"]:
            pos = state["position"]
            pnl = calc_position_pnl(pos["cost"], price, pos["quantity"] * 1000)
            _record_sell(pos, price, pnl["pnl_amount"])
            state["sold_today"] = True
            state["cash"] += price * pos["quantity"] * 1000
            state["position"] = None
            print(f"  ✅ 模擬賣出，損益：{pnl['pnl_amount']:+,.0f} 台幣 ({pnl['pnl_pct']:+.1f}%)")

        time.sleep(interval_sec)

    # ── 結算 ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print("  📋 模擬結束，今日結算")
    print(f"{'='*55}")

    if state["position"]:
        pos = state["position"]
        last_price = fake_price(base_price, rounds)
        pnl = calc_position_pnl(pos["cost"], last_price, pos["quantity"] * 1000)
        print(f"  持倉未平倉：{pos['stock_code']} 未實現損益 {pnl['pnl_amount']:+,.0f} 台幣")

    records = load_trades()
    total = calc_total_pnl(records)
    win_rate = calc_win_rate(records)
    print(f"  歷史總損益：{total:+,.0f} 台幣")
    print(f"  歷史勝率：{win_rate*100:.1f}%")
    print(f"  剩餘現金：{state['cash']:,.0f} 台幣")
    print(f"\n  AI 決策記錄已存入 data/ai_log.jsonl")
    print(f"  啟動儀表板查看：streamlit run app.py\n")


def _record_sell(pos: dict, price: float, pnl_amount: float) -> None:
    save_trade(TradeRecord(
        stock_code=pos["stock_code"],
        action=TradeAction.SELL,
        quantity=pos["quantity"] * 1000,
        price=price,
        pnl=pnl_amount,
        trade_date=date.today(),
    ))


if __name__ == "__main__":
    import sys
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    run_simulation(rounds=rounds, interval_sec=3)
