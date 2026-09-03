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
import time
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

#: 最後一次登入失敗的原因。ensure_connected 刻意吞掉例外（呼叫端多半只在乎
#: 成功與否），但「為什麼失敗」決定了該怎麼修——SDK 版本過舊與憑證錯誤的
#: 處理方式完全不同，只回 None 會讓人往錯的方向查。
_LAST_LOGIN_ERROR: Optional[str] = None


def last_login_error() -> Optional[str]:
    return _LAST_LOGIN_ERROR


def ensure_connected(
    api_key: str,
    secret_key: str,
    simulation: bool = True,
) -> Optional[sj.Shioaji]:
    """Login to Shioaji. Returns api on success, None on failure."""
    global _LAST_LOGIN_ERROR
    try:
        api = sj.Shioaji(simulation=simulation)
        # shioaji >= 1.7 的 login 已移除 fetch_contract（contracts 自動抓取）。
        # 舊版需要它，新版傳了會拋 TypeError——而那個錯誤訊息跟「憑證錯誤」
        # 長得完全不一樣，升級當天很容易誤判成帳號問題。兩個版本都支援。
        try:
            api.login(api_key=api_key, secret_key=secret_key, fetch_contract=True)
        except TypeError:
            log.debug("login 不接受 fetch_contract（新版 SDK），改用預設行為")
            api.login(api_key=api_key, secret_key=secret_key)
        _LAST_LOGIN_ERROR = None
        log.info("Shioaji connected (simulation=%s)", simulation)
        try:
            import connection_watchdog
            connection_watchdog.report_success()
        except Exception:
            pass
        return api
    except Exception as e:
        _LAST_LOGIN_ERROR = str(e)
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


def wait_for_market_data(
    api,
    probe_code: str = "2330",
    max_attempts: int = 20,
    interval: float = 3.0,
    sleep_fn=time.sleep,
) -> bool:
    """探測 Shioaji 行情是否就緒（登入成功 ≠ 報價 session 就緒）。

    ensure_connected 的 login(fetch_contract=True) 回傳只代表「登入成功」；
    底層 solace 報價 session 仍在非同步暖機中，過早呼叫 snapshots 會得到
    'Not ready'。8:30 全市場選股（daytrading_report._get_stock_universe →
    batch_fetch_snapshots）在真實模式**沒有 yfinance 備援**，報價未就緒就會
    抓到空清單 → 零候選 → dt_prediction_log 空。

    本函數以一檔高流動性股票（預設台積電 2330）反覆探測 get_snapshot，直到
    取得有效報價（close > 0）或用盡 max_attempts。

    參數
    ----
    probe_code   : 探測用股票代號（需高流動性、盤前即有前收價）
    max_attempts : 最多探測次數（預設 20）
    interval     : 每次探測間隔秒數（預設 3；20×3 = 最多等 ~60 秒）
    sleep_fn     : 等待函數（測試可注入 mock，避免真的 sleep）

    回傳
    ----
    True  = 行情就緒，呼叫端可安心選股。
    False = 逾時未就緒或 api 不存在；呼叫端應以降級資料繼續（行為與現況相同，
            不比修復前更糟），並可對外告警。
    """
    if api is None:
        return False
    for attempt in range(1, max_attempts + 1):
        snap = get_snapshot(api, probe_code)
        if snap and snap.get("close") and snap["close"] > 0:
            log.info("Shioaji 行情就緒（第 %d 次探測，probe=%s）", attempt, probe_code)
            return True
        if attempt < max_attempts:
            sleep_fn(interval)
    log.warning(
        "Shioaji 行情探測 %d 次仍未就緒（約 %.0f 秒）— 選股將以降級資料執行",
        max_attempts, max_attempts * interval,
    )
    return False


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

                    # CAS claim：下賣單前原子搶佔 active→closed，避免與
                    # 5 分鐘輪詢路徑（main._run_dt_sell_alerts）對同一持倉
                    # 重複下單。列不存在（不在狀態機中）→ 出場安全優先，
                    # 照舊下單但 log.error；claim 檢查本身失敗也照舊下單。
                    proceed_sell = True
                    claimed = False
                    if quantity > 0:
                        try:
                            import dt_position_store as _dps
                            status = _dps.get_status(code)
                            if status is None:
                                log.error(
                                    "AlertWorker: %s 不在今日持倉狀態機中，"
                                    "仍執行出場（請人工確認持倉來源）", code,
                                )
                            else:
                                claimed = _dps.claim_for_close(code)
                                if not claimed:
                                    proceed_sell = False
                                    log.info(
                                        "AlertWorker: %s 已由其他路徑出場"
                                        "（claim 未取得），跳過賣單", code,
                                    )
                        except Exception as claim_err:
                            log.warning(
                                "AlertWorker: claim 檢查失敗（照舊執行出場）%s: %s",
                                code, claim_err,
                            )

                    if quantity > 0 and proceed_sell:
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
                                # 賣單失敗 → 回滾 claim，讓輪詢路徑下輪重試
                                if claimed:
                                    try:
                                        import dt_position_store as _dps
                                        _dps.revert_to_active(code)
                                    except Exception as rv_err:
                                        log.warning(
                                            "AlertWorker: revert_to_active(%s) 失敗: %s",
                                            code, rv_err,
                                        )
                        except Exception as ex:
                            log.error("AlertWorker auto-execute exception %s: %s", code, ex)
                    elif quantity <= 0:
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
