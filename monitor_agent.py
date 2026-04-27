from __future__ import annotations

"""
Monitor agent: watches Shioaji simulation ticks, fires price alerts.

Flow:
  MonitorAgent.start()
    └─ ensure_connected()          → sj.Shioaji(simulation=True).login()
    └─ AlertWorker thread          → drains queue, saves to DB, sends Telegram
    └─ _poll_loop() thread         → api.snapshots() every POLL_INTERVAL seconds,
                                     calls check_price_alerts(), enqueues hits
"""

import os
import queue
import threading
import time
from datetime import datetime
from typing import Optional

import shioaji as sj

from logger import get_logger
from research_db import init_db, save_alert

log = get_logger(__name__)

POLL_INTERVAL = int(os.getenv("MONITOR_POLL_SECONDS", "30"))


# ── Pure logic ────────────────────────────────────────────────────────────────

def check_price_alerts(code: str, current_price: float, pick: dict) -> list[dict]:
    """
    Compare current_price against target_price and stop_loss_price in pick.
    Returns a (possibly empty) list of alert dicts ready for save_alert().
    """
    alerts = []
    target    = pick.get("target_price")
    stop_loss = pick.get("stop_loss_price")
    name      = pick.get("name", code)

    if target is not None and current_price >= target:
        alerts.append({
            "code":       code,
            "name":       name,
            "alert_type": "target_hit",
            "message":    f"{code} {name} 達到目標價 {target}，現價 {current_price}",
            "severity":   "high",
            "created_at": datetime.now(),
        })

    if stop_loss is not None and current_price <= stop_loss:
        alerts.append({
            "code":       code,
            "name":       name,
            "alert_type": "stop_loss",
            "message":    f"{code} {name} 觸及停損價 {stop_loss}，現價 {current_price}",
            "severity":   "high",
            "created_at": datetime.now(),
        })

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


# ── Telegram helper (thin wrapper so AlertWorker can mock it) ─────────────────

def send_telegram(chat_id: str, message: str) -> None:
    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=5,
        )
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


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
                save_alert(alert, self._db_path)
                if self._telegram_chat_id:
                    send_telegram(self._telegram_chat_id, alert["message"])
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
    ) -> None:
        self._api_key          = api_key
        self._secret_key       = secret_key
        self._simulation       = simulation
        self._db_path          = db_path
        self._telegram_chat_id = telegram_chat_id

        self.running: bool            = False
        self._api: Optional[sj.Shioaji] = None
        self._watchlist: list[dict]   = []
        self._alert_queue: queue.Queue = queue.Queue()
        self._worker: Optional[AlertWorker] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread]   = None

        init_db(db_path)

    def set_watchlist(self, picks: list[dict]) -> None:
        self._watchlist = picks

    def start(self) -> None:
        self._api = ensure_connected(self._api_key, self._secret_key, self._simulation)
        self.running = True

        self._worker = AlertWorker(self._alert_queue, self._db_path, self._telegram_chat_id)
        self._worker_thread = self._worker.start_thread()

        if self._api is not None:
            self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_thread.start()

        log.info("MonitorAgent started (simulation=%s)", self._simulation)

    def stop(self) -> None:
        self.running = False
        self._alert_queue.put(None)  # poison pill for worker
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        log.info("MonitorAgent stopped")

    def _poll_loop(self) -> None:
        while self.running:
            for pick in self._watchlist:
                code = pick.get("code")
                if not code:
                    continue
                snap = get_snapshot(self._api, code)
                if snap is None:
                    continue
                alerts = check_price_alerts(code, snap["close"], pick)
                for alert in alerts:
                    self._alert_queue.put(alert)
            time.sleep(POLL_INTERVAL)
