"""settle_positions 的 per_stock 必須帶 entry_price。

2026-08-11 收盤結算實際炸掉：

    File "telegram_bot.py", line 1000, in handle_settle_now
        f"　{s['entry_price']:.1f}→{s['closing_price']:.1f}"
    KeyError: 'entry_price'

先前這個錯誤被 launchd 的 exit 78（plist 的 log 路徑指向外接碟，job 根本
起不來）掩蓋著，排程修好之後才浮出來。

責任在生產端而不是呼叫端：`settle_positions` 的職責就是產出「這筆持倉買
多少、收多少、賺賠多少」，進場價本來就是結算摘要的一部分，而且 `pos.entry_price`
就在手上。兩個呼叫端（telegram_bot 的推播、app.py 的畫面）都獨立假設了這個
欄位存在，說明缺的是資料而不是防禦。
"""

from __future__ import annotations

import pytest


def _make_position(**kw):
    """建一個最小可結算的 SimPosition。

    注意：專案裡有兩個同名的 SimPosition（sim_engine 與 sim_position_store），
    欄位不同。sim_settlement 用的是 sim_position_store 這個（entry_price /
    shares / is_fractional），別拿錯。
    """
    from sim_position_store import SimPosition

    defaults = dict(
        code="2330",
        name="台積電",
        entry_price=1000.0,
        shares=0,
        quantity=1,
        is_fractional=False,
        plan_type="balanced",
        entry_date="2026-08-11",
    )
    defaults.update(kw)
    return SimPosition(**defaults)


def test_per_stock_includes_entry_price():
    """結算明細必須帶進場價——兩個呼叫端都靠它顯示「買入→收盤」。"""
    from sim_settlement import settle_positions

    pos = _make_position(entry_price=1000.0)
    result = settle_positions([pos], {"2330": 1100.0})

    assert result["per_stock"], "應該要有一筆明細"
    row = result["per_stock"][0]
    assert "entry_price" in row, "缺 entry_price，呼叫端會 KeyError"
    assert row["entry_price"] == pytest.approx(1000.0)


def test_entry_price_matches_the_position_not_the_close():
    """entry_price 要是進場價，不可以誤填成收盤價。"""
    from sim_settlement import settle_positions

    pos = _make_position(entry_price=800.0)
    row = settle_positions([pos], {"2330": 950.0})["per_stock"][0]

    assert row["entry_price"] == pytest.approx(800.0)
    assert row["closing_price"] == pytest.approx(950.0)


def test_existing_keys_are_unchanged():
    """既有欄位不可因為新增而變動——app.py 與 telegram_bot 都依賴它們。"""
    from sim_settlement import settle_positions

    row = settle_positions([_make_position()], {"2330": 1100.0})["per_stock"][0]

    for key in ("code", "name", "pnl", "closing_price"):
        assert key in row, f"既有欄位 {key} 不見了"


def test_settlement_summary_formats_without_keyerror():
    """重現當初炸掉的那一行格式化，確認不再拋 KeyError。"""
    from sim_settlement import settle_positions

    rows = settle_positions([_make_position()], {"2330": 1100.0})["per_stock"]

    # 與 telegram_bot.handle_settle_now / app.py 相同的字串組法
    for s in rows:
        line = (
            f"{s['code']} {s['name']}"
            f"　{s['entry_price']:.1f}→{s['closing_price']:.1f}"
            f"　{s['pnl']:+,.0f} 元"
        )
        assert "2330" in line
