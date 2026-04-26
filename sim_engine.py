from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from research_db import _conn, init_db


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class SimPosition:
    execution_id:  str
    code:          str
    name:          str
    quantity:      int
    avg_cost:      float
    opened_at:     datetime
    status:        str              # "open" | "closed"
    current_price: Optional[float] = None

    @property
    def unrealized_pnl(self) -> float:
        if self.current_price is None:
            return 0.0
        return (self.current_price - self.avg_cost) * self.quantity * 1000


@dataclass
class SimPortfolio:
    execution_id:   str
    plan_type:      str
    open_positions: list[SimPosition]
    realized_pnl:   float
    total_trades:   int

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + sum(p.unrealized_pnl for p in self.open_positions)


@dataclass
class SimReport:
    execution_id:        str
    plan_type:           str
    report_date:         date
    total_pnl:           float
    realized_pnl:        float
    unrealized_pnl:      float
    return_pct:          float
    total_trades:        int
    win_trades:          int
    open_positions_count: int
    narrative:           str

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.win_trades / self.total_trades * 100


# ── DB helpers ────────────────────────────────────────────────────────────────

def open_position(
    execution_id: str, code: str, name: str,
    quantity: int, price: float, db_path: str,
) -> None:
    with _conn(db_path) as con:
        row = con.execute(
            "SELECT id, quantity, avg_cost FROM sim_positions "
            "WHERE execution_id=? AND code=? AND status='open'",
            (execution_id, code),
        ).fetchone()
        if row:
            old_qty  = row[1]
            old_cost = row[2]
            new_qty  = old_qty + quantity
            new_cost = (old_cost * old_qty + price * quantity) / new_qty
            con.execute(
                "UPDATE sim_positions SET quantity=?, avg_cost=? WHERE id=?",
                (new_qty, new_cost, row[0]),
            )
        else:
            con.execute(
                "INSERT INTO sim_positions (execution_id,code,name,quantity,avg_cost,opened_at,status) "
                "VALUES (?,?,?,?,?,?,?)",
                (execution_id, code, name, quantity, price,
                 datetime.now().isoformat(), "open"),
            )


def close_position(
    execution_id: str, code: str, sell_price: float, db_path: str,
) -> float:
    with _conn(db_path) as con:
        row = con.execute(
            "SELECT id, quantity, avg_cost, name FROM sim_positions "
            "WHERE execution_id=? AND code=? AND status='open'",
            (execution_id, code),
        ).fetchone()
        if not row:
            return 0.0
        pos_id, qty, avg_cost, name = row
        realized = (sell_price - avg_cost) * qty * 1000
        con.execute(
            "UPDATE sim_positions SET status='closed' WHERE id=?", (pos_id,)
        )
        con.execute(
            "INSERT INTO sim_realized (execution_id,code,name,quantity,avg_cost,sell_price,realized_pnl,closed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (execution_id, code, name, qty, avg_cost, sell_price,
             realized, datetime.now().isoformat()),
        )
    return realized


def update_unrealized(
    execution_id: str, prices: dict[str, float], db_path: str,
) -> None:
    with _conn(db_path) as con:
        for code, price in prices.items():
            con.execute(
                "UPDATE sim_positions SET current_price=? "
                "WHERE execution_id=? AND code=? AND status='open'",
                (price, execution_id, code),
            )


def get_portfolio(execution_id: str, db_path: str) -> SimPortfolio:
    with _conn(db_path) as con:
        rows = con.execute(
            "SELECT code,name,quantity,avg_cost,current_price,opened_at "
            "FROM sim_positions WHERE execution_id=? AND status='open'",
            (execution_id,),
        ).fetchall()
        realized_rows = con.execute(
            "SELECT realized_pnl FROM sim_realized WHERE execution_id=?",
            (execution_id,),
        ).fetchall()
        trade_count = con.execute(
            "SELECT COUNT(*) FROM sim_realized WHERE execution_id=?",
            (execution_id,),
        ).fetchone()[0]
        plan_row = con.execute(
            "SELECT plan_type FROM plan_executions WHERE execution_id=?",
            (execution_id,),
        ).fetchone()

    realized_pnl = sum(r[0] for r in realized_rows)
    positions = [
        SimPosition(
            execution_id=execution_id,
            code=r[0], name=r[1], quantity=r[2],
            avg_cost=r[3], current_price=r[4],
            opened_at=datetime.fromisoformat(r[5]),
            status="open",
        )
        for r in rows
    ]
    return SimPortfolio(
        execution_id=execution_id,
        plan_type=plan_row[0] if plan_row else "",
        open_positions=positions,
        realized_pnl=realized_pnl,
        total_trades=trade_count,
    )


# ── Report ────────────────────────────────────────────────────────────────────

def generate_sim_report(
    execution_id: str, plan_type: str,
    initial_capital: float, db_path: str,
) -> SimReport:
    portfolio = get_portfolio(execution_id, db_path)
    with _conn(db_path) as con:
        win_count = con.execute(
            "SELECT COUNT(*) FROM sim_realized WHERE execution_id=? AND realized_pnl > 0",
            (execution_id,),
        ).fetchone()[0]

    unrealized = sum(p.unrealized_pnl for p in portfolio.open_positions)
    total_pnl  = portfolio.realized_pnl + unrealized
    return_pct = (total_pnl / initial_capital * 100) if initial_capital else 0.0

    plan_labels = {"aggressive": "衝刺版", "balanced": "均衡版", "conservative": "保守版"}
    label = plan_labels.get(plan_type, plan_type)
    on_track = "✅ 領先" if return_pct > 0 else "❌ 落後"
    narrative = (
        f"【{label}】累積報酬 {return_pct:+.2f}%　{on_track}\n"
        f"已實現損益 {portfolio.realized_pnl:+,.0f} 元　"
        f"未實現損益 {unrealized:+,.0f} 元　"
        f"持有 {len(portfolio.open_positions)} 支股票"
    )

    return SimReport(
        execution_id=execution_id,
        plan_type=plan_type,
        report_date=date.today(),
        total_pnl=total_pnl,
        realized_pnl=portfolio.realized_pnl,
        unrealized_pnl=unrealized,
        return_pct=return_pct,
        total_trades=portfolio.total_trades,
        win_trades=win_count,
        open_positions_count=len(portfolio.open_positions),
        narrative=narrative,
    )


def compare_plans(
    exec_map: dict[str, str],
    initial_capital: float,
    db_path: str,
) -> list[SimReport]:
    reports = [
        generate_sim_report(exec_id, plan_type, initial_capital, db_path)
        for plan_type, exec_id in exec_map.items()
    ]
    return sorted(reports, key=lambda r: r.total_pnl, reverse=True)
