from __future__ import annotations

"""
Monitor agent: watches Shioaji ticks, fires price alerts.

Flow:
  MonitorAgent.start()
    └─ ensure_connected()   → sj.Shioaji(simulation=True).login()
    └─ AlertWorker thread   → drains queue, saves to DB, sends Telegram
    └─ _subscribe_ticks()   → api.quote.subscribe() per position
                               on_tick_stk_v1 callback → check_price_alerts()
                               → enqueue alert if triggered
"""

import queue
import threading
from datetime import datetime
from typing import Optional

import shioaji as sj
from shioaji.constant import QuoteType, QuoteVersion

from logger import get_logger
from research_db import init_db, save_alert, mark_alert_sent

log = get_logger(__name__)


# ── Pure logic ────────────────────────────────────────────────────────────────

_TRAILING_START_PCT = 3.0   # 漲到 3% 才啟動移動停損（預設值，可由 MonitorAgent 覆蓋）
_TRAILING_GAP_PCT   = 2.0   # 移動停損永遠在最高點下方 2%（預設值）


def check_price_alerts(
    code: str,
    current_price: float,
    pick: dict,
    trailing_start_pct: float = _TRAILING_START_PCT,
    trailing_gap_pct: float = _TRAILING_GAP_PCT,
) -> list[dict]:
    """
    每個 tick 呼叫一次。pick 是 mutable dict，會在這裡更新 peak_price。

    參數
    ----
    trailing_start_pct : 移動停損啟動門檻（預設 3%）
    trailing_gap_pct   : 停損點與最高點的距離（預設 2%）

    觸發規則（依優先順序）：
    1. 移動停損：進場後漲超過 trailing_start_pct，停損跟著最高點 -trailing_gap_pct
    2. AI 目標價：現價 >= target_price
    3. AI 停損價：現價 <= stop_loss_price（移動停損未啟動前的保護）
    """
    alerts = []
    name         = pick.get("name", code)
    entry_price  = pick.get("entry_price")
    target       = pick.get("target_price")
    stop_loss    = pick.get("stop_loss_price")
    now          = datetime.now()

    def _alert(alert_type: str, message: str, **extra) -> dict:
        return {
            "code": code, "name": name,
            "alert_type": alert_type, "message": message,
            "severity": "high", "created_at": now,
            "current_price": current_price,
            **extra,
        }

    # ── 移動停損（有進場價才能算）──────────────────────────────────
    if entry_price is not None and entry_price > 0:
        gain_pct = (current_price - entry_price) / entry_price * 100

        # 更新最高點（只升不降）
        peak = pick.get("peak_price") or entry_price
        if current_price > peak:
            pick["peak_price"] = current_price
            peak = current_price

        peak_gain_pct = (peak - entry_price) / entry_price * 100

        if peak_gain_pct >= trailing_start_pct:
            trailing_stop = peak * (1 - trailing_gap_pct / 100)
            if current_price <= trailing_stop:
                return [_alert(
                    "trailing_stop",
                    f"{name} 移動停損觸發：最高 +{peak_gain_pct:.1f}%，"
                    f"現價 +{gain_pct:.1f}%，停損 {trailing_stop:,.1f}",
                    trailing_stop=trailing_stop,
                    peak_price=peak,
                )]
            # 啟動中，跳過固定停損（避免重複警報）
            return alerts

    # ── AI 目標價 ──────────────────────────────────────────────────
    if target is not None and current_price >= target:
        alerts.append(_alert(
            "target_hit",
            f"{name} 達到目標價 {target:,.1f}，現價 {current_price:,.1f}",
            target_price=target,
        ))

    # ── AI 停損價（移動停損未啟動前的保護）──────────────────────────
    if stop_loss is not None and current_price <= stop_loss:
        alerts.append(_alert(
            "stop_loss",
            f"{name} 觸及停損價 {stop_loss:,.1f}，現價 {current_price:,.1f}",
            stop_loss_price=stop_loss,
        ))

    return alerts


# ── Shioaji helpers ───────────────────────────────────────────────────────────

def ensure_connected(
    api_key: str,
    secret_key: str,
    simulation: bool = True,
) -> Optional[sj.Shioaji]:
    """Login to Shioaji. Returns api on success, None on failure."""
    try:
        api = sj.Shioaji(simulation=simulation)
        api.login(api_key=api_key, secret_key=secret_key, fetch_contract=True)
        log.info("Shioaji connected (simulation=%s)", simulation)
        return api
    except Exception as e:
        log.error("Shioaji connection failed: %s", e)
        return None


def get_snapshot(api: sj.Shioaji, code: str) -> Optional[dict]:
    """Return snapshot dict {close, volume, change_price} or None on error."""
    try:
        contract = api.Contracts.Stocks.get(code)
        if not contract:
            return None
        snaps = api.snapshots([contract])
        if not snaps:
            return None
        s = snaps[0]
        return {
            "close":        s.close,
            "volume":       s.total_volume,
            "change_price": s.change_price,
        }
    except Exception as e:
        log.warning("get_snapshot(%s) error: %s", code, e)
        return None


# ── AlertWorker ───────────────────────────────────────────────────────────────

class AlertWorker:
    """
    Background worker that drains an alert Queue.
    Send None as a poison pill to stop the worker.
    """

    def __init__(
        self,
        alert_queue: queue.Queue,
        db_path: str,
        telegram_chat_id: Optional[str],
    ) -> None:
        self._q              = alert_queue
        self._db_path        = db_path
        self._telegram_chat_id = telegram_chat_id

    def run(self) -> None:
        """Process alerts until poison pill (None) is received."""
        while True:
            alert = self._q.get()
            if alert is None:
                break
            try:
                alert_id = save_alert(alert, self._db_path)
                from notifier import notify_price_alert
                notify_price_alert(
                    code=alert.get("code", ""),
                    name=alert.get("name", ""),
                    alert_type=alert.get("alert_type", ""),
                    current_price=alert.get("current_price", 0),
                    target_price=alert.get("target_price"),
                    stop_loss_price=alert.get("stop_loss_price"),
                )
                mark_alert_sent(alert_id, self._db_path)
            except Exception as e:
                log.error("AlertWorker error: %s", e)

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t


# ── MonitorAgent ──────────────────────────────────────────────────────────────

class MonitorAgent:
    """
    Orchestrates Shioaji snapshot polling and alert dispatch.

    Usage:
        agent = MonitorAgent(api_key=..., secret_key=..., simulation=True,
                             db_path=..., telegram_chat_id=...)
        agent.set_watchlist(picks)  # list of validated pick dicts
        agent.start()
        ...
        agent.stop()
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        simulation: bool,
        db_path: str,
        telegram_chat_id: Optional[str],
        api: Optional[sj.Shioaji] = None,
        trailing_start_pct: float = _TRAILING_START_PCT,
        trailing_gap_pct: float = _TRAILING_GAP_PCT,
    ) -> None:
        self._api_key              = api_key
        self._secret_key           = secret_key
        self._simulation           = simulation
        self._db_path              = db_path
        self._telegram_chat_id     = telegram_chat_id
        self._trailing_start_pct   = trailing_start_pct
        self._trailing_gap_pct     = trailing_gap_pct

        self.running: bool              = False
        self._api: Optional[sj.Shioaji] = api
        self._watchlist: list[dict]     = []
        self._alert_queue: queue.Queue  = queue.Queue()
        self._worker: Optional[AlertWorker]        = None
        self._worker_thread: Optional[threading.Thread] = None
        self._subscribed_codes: list[str] = []

        init_db(db_path)

    def set_watchlist(self, picks: list[dict]) -> None:
        self._watchlist = picks

    def start(self) -> None:
        if self._api is None:
            self._api = ensure_connected(self._api_key, self._secret_key, self._simulation)
        self.running = True

        self._worker = AlertWorker(self._alert_queue, self._db_path, self._telegram_chat_id)
        self._worker_thread = self._worker.start_thread()

        if self._api is not None:
            self._subscribe_ticks()

        log.info("MonitorAgent started (simulation=%s)", self._simulation)

    def stop(self) -> None:
        self.running = False
        if self._api is not None:
            self._unsubscribe_ticks()
        self._alert_queue.put(None)  # poison pill for worker
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        log.info("MonitorAgent stopped")

    def _subscribe_ticks(self) -> None:
        """每支持倉股票訂閱 tick，有新成交就立刻比對停損/目標價。"""
        watchlist = self._watchlist
        trailing_start = self._trailing_start_pct
        trailing_gap   = self._trailing_gap_pct

        @self._api.on_tick_stk_v1()
        def _on_tick(exchange, tick):
            price = float(tick.close)
            for pick in watchlist:
                if pick.get("code") == tick.code:
                    alerts = check_price_alerts(
                        tick.code, price, pick,
                        trailing_start_pct=trailing_start,
                        trailing_gap_pct=trailing_gap,
                    )
                    for a in alerts:
                        self._alert_queue.put(a)
                    break

        for pick in watchlist:
            code = pick.get("code")
            if not code:
                continue
            contract = self._api.Contracts.Stocks.get(code)
            if contract is None:
                continue
            try:
                self._api.quote.subscribe(
                    contract,
                    quote_type=QuoteType.Tick,
                    version=QuoteVersion.v1,
                )
                self._subscribed_codes.append(code)
                log.info("subscribed tick: %s", code)
            except Exception as e:
                log.warning("subscribe(%s) failed: %s", code, e)

    def _unsubscribe_ticks(self) -> None:
        for code in self._subscribed_codes:
            contract = self._api.Contracts.Stocks.get(code)
            if contract is None:
                continue
            try:
                self._api.quote.unsubscribe(
                    contract,
                    quote_type=QuoteType.Tick,
                    version=QuoteVersion.v1,
                )
            except Exception as e:
                log.debug("unsubscribe(%s) failed: %s", code, e)
        self._subscribed_codes.clear()
