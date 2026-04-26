from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from pathlib import Path

TRADES_FILE = Path(__file__).parent / "data" / "trades.csv"

FIELDNAMES = ["stock_code", "action", "quantity", "price", "pnl", "trade_date"]


class TradeAction(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class TradeRecord:
    stock_code: str
    action: TradeAction
    quantity: float
    price: float
    pnl: float
    trade_date: date


def save_trade(record: TradeRecord, path: Path = TRADES_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if needs_header:
            writer.writeheader()
        writer.writerow({
            "stock_code": record.stock_code,
            "action": record.action.value,
            "quantity": record.quantity,
            "price": record.price,
            "pnl": record.pnl,
            "trade_date": record.trade_date.isoformat(),
        })


def load_trades(path: Path = TRADES_FILE) -> list[TradeRecord]:
    if not path.exists():
        return []
    records = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            records.append(TradeRecord(
                stock_code=row["stock_code"],
                action=TradeAction(row["action"]),
                quantity=float(row["quantity"]),
                price=float(row["price"]),
                pnl=float(row["pnl"]),
                trade_date=date.fromisoformat(row["trade_date"]),
            ))
    return records


def _sell_trades(records: list[TradeRecord]) -> list[TradeRecord]:
    return [r for r in records if r.action == TradeAction.SELL]


def calc_total_pnl(records: list[TradeRecord]) -> float:
    return sum(r.pnl for r in _sell_trades(records))


def calc_win_rate(records: list[TradeRecord]) -> float:
    sells = _sell_trades(records)
    if not sells:
        return 0.0
    wins = sum(1 for r in sells if r.pnl > 0)
    return wins / len(sells)
