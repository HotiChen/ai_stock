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
from datetime import date, datetime
from typing import Optional

import shioaji as sj
from shioaji.constant import QuoteType, QuoteVersion

from atomic_json import atomic_read_json, atomic_write_json
from logger import get_logger
from executor import force_stop_loss
from notifier import notify_price_alert
from research_db import init_db, save_alert, mark_alert_sent, save_daily_trade

# 波段移動停損最高點 sidecar（重啟後還原 peak，避免誤觸發移動停損）
_DEFAULT_PEAKS_PATH = "data/wave_peaks.json"

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
        try:
            import connection_watchdog
            connection_watchdog.report_success()
        except Exception:
            pass
        return api
    except Exception as e:
        log.error("Shioaji connection failed: %s", e)
        try:
            import connection_watchdog
            connection_watchdog.report_failure(str(e))
        except Exception:
            pass
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
        result = {
            "close":        s.close,
            "volume":       s.total_volume,
            "change_price": s.change_price,
        }
        try:
            import connection_watchdog
            connection_watchdog.report_success()
        except Exception:
            pass
        return result
    except Exception as e:
        log.warning("get_snapshot(%s) error: %s", code, e)
        try:
            import connection_watchdog
            connection_watchdog.report_failure(str(e))
        except Exception:
            pass
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
        auto_execute: bool = False,
        api=None,
        watchlist: Optional[list] = None,
    ) -> None:
        self._q                = alert_queue
        self._db_path          = db_path
        self._telegram_chat_id = telegram_chat_id
        self._auto_execute     = auto_execute
        self._api              = api
        # 修正六：過濾沒有 code 的 pick，避免 None 當 key
        self._watchlist        = {
            p["code"]: p
            for p in (watchlist or [])
            if p.get("code")
        }

    def run(self) -> None:
        """Process alerts until poison pill (None) is received."""
        while True:
            alert = self._q.get()
            if alert is None:
                break
            try:
                alert_id = save_alert(alert, self._db_path)
                notify_price_alert(
                    code=alert.get("code", ""),
                    name=alert.get("name", ""),
                    alert_type=alert.get("alert_type", ""),
                    current_price=alert.get("current_price", 0),
                    target_price=alert.get("target_price"),
                    stop_loss_price=alert.get("stop_loss_price"),
                )
                mark_alert_sent(alert_id, self._db_path)

                # 若啟用自動執行且為停損/追蹤停利警報，直接下市價賣單
                if (
                    self._auto_execute
                    and self._api is not None
                    and alert.get("alert_type") in ("stop_loss", "trailing_stop")
                ):
                    code = alert.get("code", "")
                    pick = self._watchlist.get(code, {})
                    quantity = pick.get("quantity", 0)
                    lot_type = pick.get("lot_type", "common")
                    name     = pick.get("name", code)
                    if quantity > 0:
                        try:
                            success = force_stop_loss(
                                api=self._api,
                                code=code,
                                name=name,
                                quantity=quantity,
                                lot_type=lot_type,
                            )
                            if success:
                                log.warning(
                                    "AlertWorker auto-execute: 停損/追蹤停利賣出 %s qty=%d lot=%s",
                                    code, quantity, lot_type,
                                )
                                # GAP 1：把自動出場寫入 daily_trades，
                                # 讓 PostMarketJob 能計算其損益、歸因出場原因。
                                # 包在 try/except，DB 失敗絕不可中斷 alert thread。
                                #
                                # 防重複：同一檔同一天輪詢路徑（main._run_dt_sell_alerts）
                                # 也可能出場並寫入 auto_exit sell；且同一 code 的 tick
                                # 警報可能連續入列多次。寫入前檢查今日是否已有該 code
                                # 的 auto_exit sell 記錄，避免已實現損益被重複計算。
                                try:
                                    from research_db import load_daily_trades
                                    already_recorded = any(
                                        t.get("code") == code
                                        and t.get("action") == "sell"
                                        and t.get("note") == "auto_exit"
                                        for t in load_daily_trades(
                                            date.today(), self._db_path)
                                    )
                                    if already_recorded:
                                        log.info(
                                            "AlertWorker: %s 今日已有 auto_exit "
                                            "出場記錄，跳過重複寫入", code,
                                        )
                                    else:
                                        exit_price = alert.get("current_price")
                                        entry_price = pick.get("entry_price")
                                        # common 的 quantity 是「張」（1 張 =
                                        # 1000 股），intraday_odd 的 quantity 是
                                        # 「股」——pnl 以股數計，common 須乘 1000。
                                        multiplier = (
                                            1000 if lot_type == "common" else 1
                                        )
                                        if (
                                            entry_price is not None
                                            and exit_price is not None
                                            and quantity
                                        ):
                                            pnl = ((exit_price - entry_price)
                                                   * quantity * multiplier)
                                        else:
                                            pnl = None
                                        save_daily_trade({
                                            "trade_date":  date.today(),
                                            "code":        code,
                                            "name":        name,
                                            "action":      "sell",
                                            "quantity":    quantity,
                                            "price":       exit_price,
                                            "pnl":         pnl,
                                            "lot_type":    lot_type,
                                            "sector":      pick.get("sector", "未知"),
                                            "note":        "auto_exit",
                                            "exit_reason": alert.get("alert_type"),
                                        }, self._db_path)
                                except Exception as rec_err:
                                    log.error(
                                        "AlertWorker auto-execute: 記錄出場失敗 %s: %s",
                                        code, rec_err,
                                    )
                            else:
                                log.error(
                                    "AlertWorker auto-execute: force_stop_loss 失敗 %s", code,
                                )
                        except Exception as ex:
                            log.error("AlertWorker auto-execute exception %s: %s", code, ex)
                    else:
                        log.warning(
                            "AlertWorker auto-execute: %s quantity=0，跳過自動停損", code,
                        )

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
        auto_execute: bool = False,
        peaks_path: str = _DEFAULT_PEAKS_PATH,
    ) -> None:
        self._api_key              = api_key
        self._secret_key           = secret_key
        self._simulation           = simulation
        self._db_path              = db_path
        self._telegram_chat_id     = telegram_chat_id
        self._trailing_start_pct   = trailing_start_pct
        self._trailing_gap_pct     = trailing_gap_pct
        self._auto_execute         = auto_execute
        self._peaks_path           = peaks_path

        self.running: bool              = False
        self._api: Optional[sj.Shioaji] = api
        self._watchlist: dict[str, dict] = {}
        self._alert_queue: queue.Queue  = queue.Queue()
        self._worker: Optional[AlertWorker]        = None
        self._worker_thread: Optional[threading.Thread] = None
        self._subscribed_codes: list[str] = []

        init_db(db_path)

    def set_watchlist(self, picks: list[dict]) -> None:
        # 修正六：過濾沒有 code 的 pick，避免 None 當 key
        self._watchlist = {
            p["code"]: p
            for p in (picks or [])
            if p.get("code")
        }
        self._seed_peaks_from_sidecar()

    # ── 移動停損最高點持久化（GAP 2）────────────────────────────────────────
    #
    # peak_price 只存在 in-memory watchlist dict，main.py 重啟後若不還原，
    # peak 會歸零回 entry → 移動停損誤觸發。以 sidecar JSON 持久化高水位。
    # 僅 RECORD/PERSIST peak，不改動任何出場門檻或觸發邏輯。

    def _load_peaks(self) -> dict:
        """讀取 sidecar，回傳 {code: peak_price}；檔案缺失/損毀回 {}。"""
        data = atomic_read_json(self._peaks_path)
        if not isinstance(data, dict):
            return {}
        out = {}
        for k, v in data.items():
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                continue
        return out

    def _seed_peaks_from_sidecar(self) -> None:
        """set_watchlist / start 時呼叫：以 max(entry, persisted) seed 每檔 peak。

        只 seed 今日 watchlist 內的 code；sidecar 中的 stale 條目忽略。
        """
        persisted = self._load_peaks()
        for code, pick in self._watchlist.items():
            entry = pick.get("entry_price")
            if entry is None:
                continue
            seed = entry
            if code in persisted:
                seed = max(entry, persisted[code])
            pick["peak_price"] = seed

    def _record_peak(self, code: str, price: float) -> None:
        """若 price 創新高則更新 in-memory peak 並持久化 sidecar（原子寫入）。

        peak 只升不降；非今日 watchlist 的 code 不處理。
        DB/檔案失敗不可中斷監控執行緒。
        """
        pick = self._watchlist.get(code)
        if pick is None:
            return
        entry = pick.get("entry_price") or 0.0
        peak = pick.get("peak_price") or entry
        if price <= peak:
            return
        pick["peak_price"] = price
        try:
            peaks = self._load_peaks()
            # 只保留今日 watchlist 的 code，順帶清除 stale 條目
            peaks = {c: v for c, v in peaks.items() if c in self._watchlist}
            peaks[code] = price
            atomic_write_json(self._peaks_path, peaks)
        except Exception as e:
            log.warning("wave_peaks 持久化失敗 %s: %s", code, e)

    def start(self) -> None:
        if self._api is None:
            self._api = ensure_connected(self._api_key, self._secret_key, self._simulation)
        self.running = True

        # 重啟還原移動停損高水位（若 set_watchlist 後 sidecar 才出現也能補上）
        self._seed_peaks_from_sidecar()

        self._worker = AlertWorker(
            self._alert_queue, self._db_path, self._telegram_chat_id,
            auto_execute=self._auto_execute,
            api=self._api,
            watchlist=self._watchlist,
        )
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
            pick = watchlist.get(tick.code)
            if pick is not None:
                # GAP 2：新高時持久化 peak（check_price_alerts 也會更新 in-memory，
                # 這裡額外把高水位寫入 sidecar 以利重啟還原）
                self._record_peak(tick.code, price)
                alerts = check_price_alerts(
                    tick.code, price, pick,
                    trailing_start_pct=trailing_start,
                    trailing_gap_pct=trailing_gap,
                )
                for a in alerts:
                    self._alert_queue.put(a)

        for code, pick in watchlist.items():
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
