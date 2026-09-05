"""
dt_exit_rules.py — 當沖出場規則（單一真相來源）

為什麼要獨立一個模組
--------------------
出場邏輯原本有兩份實作，門檻不同：

  monitor_agent.check_price_alerts()         tick 路徑（真實下單走這條）
      停損 = ATR 絕對價、停利 = ATR 絕對價、無天花板停利、無時間強平
  daytrading_monitor.check_trailing_stop()   5 分鐘輪詢 / 紙上模擬
      停損 = 固定 3%、停利 = 固定 9%、有時間強平

也就是說**紙上模擬跑的規則不是實盤跑的規則**。一檔 ATR 等於價格 1% 的
股票，tick 路徑在 −1.5% 出場、紙上在 −3.0% 出場——差一倍。用紙上資料
推論實盤績效因此不成立，而那正是整個複盤與回測計畫的分母。

同樣的理由，手續費常數也早就收斂成 dt_fees.py 了：比較不同策略的損益時，
分母不一致等於沒有答案。

還修掉一個隱蔽的行為缺陷
------------------------
舊版 check_price_alerts 在移動停損啟動後直接 `return alerts`（空 list），
目標價與停損價都不再檢查。而 ATR 目標價（entry + 2.5 ATR）通常大於移動
停損啟動門檻（3%），所以價格幾乎一定先啟動移動停損 → **目標價實質上永遠
不會觸發**。統一後移動停損不再提前 return，價格一路衝到目標價仍會出場。

優先順序
--------
  1. 停損        絕對價（ATR）優先；沒有才用百分比
  2. 天花板停利  百分比（漲停前強制出場，避免漲停鎖死賣不掉）
  3. 強制平倉    時間到（避免跨日交割義務）
  4. 移動停損    峰值達門檻後回落
  5. 目標價      絕對價（ATR）

保護性出場（1–3）一律優先於計畫性出場（4–5）。移動停損排在目標價之前：
已經回落的部位先出場；但因為它不再提前 return，一路上漲的部位仍會由
第 5 條在目標價出場。順序本身就是規則，改動它必須是明確的決定。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Optional

#: 出場原因。這些字串會進 DB（alert_type）與 Telegram，改名是破壞性變更。
REASON_STOP_LOSS = "stop_loss"
REASON_TAKE_PROFIT_CEILING = "take_profit_ceiling"
REASON_FORCE_CLOSE = "force_close"
REASON_TRAILING = "trailing_stop"
REASON_TARGET = "target_hit"

#: 檢查順序。測試會釘住它——順序是規則，不是實作細節。
PRIORITY = (
    REASON_STOP_LOSS,
    REASON_TAKE_PROFIT_CEILING,
    REASON_FORCE_CLOSE,
    REASON_TRAILING,
    REASON_TARGET,
)

SELL_LABEL = {
    REASON_STOP_LOSS: "停損出場",
    REASON_TAKE_PROFIT_CEILING: "天花板停利",
    REASON_FORCE_CLOSE: "強制平倉",
    REASON_TRAILING: "追蹤停利",
    REASON_TARGET: "目標價停利",
}


@dataclass
class ExitDecision:
    """出場判斷結果。should_exit=False 時其餘欄位僅供除錯。"""
    should_exit: bool
    reason: str = ""
    message: str = ""
    trigger_price: Optional[float] = None
    peak_price: Optional[float] = None
    trailing_stop_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    target_price: Optional[float] = None


def _usable(v) -> bool:
    """entry_price 為 None 或 0 代表尚未成交（watching），百分比規則不適用。

    0 在這裡一律當作「沒有值」而不是「價格是 0」——與行情層對 close==0
    的處理一致（0 是「沒有報價」，不是免費的股票）。
    """
    return v is not None and v > 0


def _parse_hhmm(s) -> Optional[dtime]:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return None


def evaluate_exit(
    *,
    entry_price: Optional[float],
    current_price: float,
    peak_price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    target_price: Optional[float] = None,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
    trailing_start_pct: Optional[float] = None,
    trailing_gap_pct: Optional[float] = None,
    force_close_time: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ExitDecision:
    """依 PRIORITY 逐條檢查，回傳第一個成立的出場理由。

    絕對價（stop_loss / target_price）來自 8:30 的 ATR 計畫；
    百分比（*_pct）來自 DaytradingConfig。兩者同時存在時**絕對價優先**——
    ATR 反映個股實際波動，固定百分比對牛皮股太寬、對飆股太窄。

    entry_price 缺失（watching 尚未成交）時只評估絕對價規則。
    """
    now = now or datetime.now()
    has_entry = _usable(entry_price)

    def _hold() -> ExitDecision:
        return ExitDecision(
            should_exit=False, trigger_price=current_price,
            peak_price=peak_price, stop_loss_price=stop_loss,
            target_price=target_price,
        )

    def _exit(reason: str, message: str, **extra) -> ExitDecision:
        return ExitDecision(
            should_exit=True, reason=reason, message=message,
            trigger_price=current_price, peak_price=peak_price,
            stop_loss_price=stop_loss, target_price=target_price, **extra,
        )

    gain_pct = ((current_price - entry_price) / entry_price * 100) if has_entry else None

    # ── 1. 停損 ───────────────────────────────────────────────────────────────
    # ATR 絕對價優先。有絕對價時**不再**檢查百分比：兩個門檻並存的話，
    # 較早觸發的那個會偷偷成為實際規則，單一真相來源就沒有意義了。
    if _usable(stop_loss):
        if current_price <= stop_loss:
            return _exit(
                REASON_STOP_LOSS,
                f"停損觸發：現價 {current_price:,.2f} 跌破停損價 {stop_loss:,.2f}"
                + (f"（{gain_pct:+.2f}%）" if gain_pct is not None else ""),
            )
    elif has_entry and stop_loss_pct is not None and gain_pct <= -stop_loss_pct:
        return _exit(
            REASON_STOP_LOSS,
            f"停損觸發：跌幅 {gain_pct:.2f}%（停損線 -{stop_loss_pct}%）",
        )

    # ── 2. 天花板停利 ─────────────────────────────────────────────────────────
    # 漲停是 ±10%，在 9% 出場是刻意不賭最後 1%：漲停打不開就賣不掉。
    if has_entry and take_profit_pct is not None and gain_pct >= take_profit_pct:
        return _exit(
            REASON_TAKE_PROFIT_CEILING,
            f"天花板停利：漲幅 {gain_pct:+.2f}%（上限 +{take_profit_pct}%）",
        )

    # ── 3. 強制平倉 ───────────────────────────────────────────────────────────
    fc = _parse_hhmm(force_close_time) if force_close_time else None
    if fc is not None and now.time() >= fc:
        return _exit(
            REASON_FORCE_CLOSE,
            f"強制平倉：到達 {force_close_time}，避免跨日交割",
        )

    # ── 4. 移動停損 ───────────────────────────────────────────────────────────
    # 舊版在這裡提前 return，把第 5 條整個封鎖掉。現在只在真的觸發時 return。
    if (has_entry and trailing_start_pct is not None
            and trailing_gap_pct is not None):
        peak = peak_price if _usable(peak_price) else entry_price
        peak_gain_pct = (peak - entry_price) / entry_price * 100
        if peak_gain_pct >= trailing_start_pct:
            line = peak * (1 - trailing_gap_pct / 100.0)
            if current_price <= line:
                drop_pct = (current_price - peak) / peak * 100
                return _exit(
                    REASON_TRAILING,
                    f"追蹤停利：高點 +{peak_gain_pct:.2f}% → 回落 {abs(drop_pct):.2f}%，"
                    f"現漲幅 {gain_pct:+.2f}%",
                    trailing_stop_price=line,
                )

    # ── 5. 目標價 ─────────────────────────────────────────────────────────────
    if _usable(target_price) and current_price >= target_price:
        return _exit(
            REASON_TARGET,
            f"達到目標價 {target_price:,.2f}，現價 {current_price:,.2f}"
            + (f"（{gain_pct:+.2f}%）" if gain_pct is not None else ""),
        )

    return _hold()


def evaluate_exit_from_config(
    *,
    config,
    entry_price: Optional[float],
    current_price: float,
    peak_price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    target_price: Optional[float] = None,
    now: Optional[datetime] = None,
) -> ExitDecision:
    """從 DaytradingConfig 取百分比門檻的轉接器（鴨型別，避免循環 import）。

    兩條出場路徑共用這個入口，門檻就不可能再各走各的。
    """
    return evaluate_exit(
        entry_price=entry_price,
        current_price=current_price,
        peak_price=peak_price,
        stop_loss=stop_loss,
        target_price=target_price,
        stop_loss_pct=getattr(config, "stop_loss_pct", None),
        take_profit_pct=getattr(config, "take_profit_pct", None),
        trailing_start_pct=getattr(config, "trailing_start_pct", None),
        trailing_gap_pct=getattr(config, "trailing_gap_pct", None),
        force_close_time=getattr(config, "force_close_time", None),
        now=now,
    )
