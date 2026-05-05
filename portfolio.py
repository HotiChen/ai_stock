from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Optional

_DEFAULT_CAPITAL = 30_000.0


# ── Position ──────────────────────────────────────────────────────────────────

@dataclass
class Position:
    code:          str
    name:          str
    quantity:      int          # 整張數（is_fractional=False 時有效）
    is_fractional: bool         # True = 零股模式
    shares:        int          # 零股股數（is_fractional=True 時有效）
    avg_cost:      float        # 每股平均成本
    entry_date:    date
    entry_reason:  str          # 從 StockPick.reason 帶入，thesis 驗證用
    current_price: float = 0.0  # 最新市價（由 update_prices 更新）

    # ── 計算屬性 ──────────────────────────────────────────────────────────────

    @property
    def total_shares(self) -> int:
        """持有總股數（整張 = quantity × 1000，零股 = shares）"""
        return self.shares if self.is_fractional else self.quantity * 1_000

    @property
    def total_cost(self) -> float:
        """持倉總成本"""
        return self.avg_cost * self.total_shares

    def market_value(self, current_price: float) -> float:
        """以指定價格計算市值"""
        return current_price * self.total_shares

    def unrealized_pnl(self, current_price: float) -> float:
        """未實現損益"""
        return self.market_value(current_price) - self.total_cost

    def unrealized_pnl_pct(self, current_price: float) -> float:
        """未實現損益率 %"""
        if self.total_cost == 0:
            return 0.0
        return self.unrealized_pnl(current_price) / self.total_cost * 100


# ── SimulatedPortfolio ────────────────────────────────────────────────────────

class SimulatedPortfolio:
    """
    模擬投資組合。
    - 選計劃後所有 picks 自動成交（simulation 模式）
    - 未來可換成 ShioajiPortfolio，介面相同
    """

    def __init__(self, initial_capital: float = _DEFAULT_CAPITAL):
        self._initial_capital = initial_capital
        self._cash             = initial_capital
        self._positions: dict[str, Position] = {}

    # ── 查詢 ──────────────────────────────────────────────────────────────────

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_position(self, code: str) -> Optional[Position]:
        return self._positions.get(code)

    def get_available_capital(self) -> float:
        return self._cash

    def get_total_value(self, prices: dict[str, float]) -> float:
        """現金 + 所有持倉市值（缺少現價的持倉用成本代替）"""
        position_value = sum(
            pos.market_value(prices.get(pos.code, pos.avg_cost))
            for pos in self._positions.values()
        )
        return self._cash + position_value

    # ── 操作 ──────────────────────────────────────────────────────────────────

    def open_position(
        self,
        code: str,
        name: str,
        quantity: int,
        price: float,
        is_fractional: bool,
        shares: int,
        reason: str,
        entry_date: date,
    ) -> None:
        """新建倉位。資金不足時 raise ValueError。"""
        if code in self._positions:
            raise ValueError(f"已持有 {code}，請使用 add_position 加碼")

        cost = self._calc_cost(price, quantity, is_fractional, shares)
        if self._cash < cost:
            raise ValueError(
                f"資金不足：需要 {cost:,.0f} 元，可用 {self._cash:,.0f} 元"
            )
        self._cash -= cost
        self._positions[code] = Position(
            code=code, name=name,
            quantity=quantity, is_fractional=is_fractional, shares=shares,
            avg_cost=price, entry_date=entry_date, entry_reason=reason,
        )

    def add_position(
        self,
        code: str,
        quantity: int,
        price: float,
        is_fractional: bool,
        shares: int,
    ) -> None:
        """加碼現有持倉，更新加權平均成本。資金不足時 raise ValueError。"""
        pos = self._positions.get(code)
        if pos is None:
            raise ValueError(f"未持有 {code}，請使用 open_position 建倉")

        cost = self._calc_cost(price, quantity, is_fractional, shares)
        if self._cash < cost:
            raise ValueError(
                f"資金不足：需要 {cost:,.0f} 元，可用 {self._cash:,.0f} 元"
            )
        add_shares = shares if is_fractional else quantity * 1_000
        old_shares = pos.total_shares
        new_shares  = old_shares + add_shares
        # 加權平均成本
        pos.avg_cost = (pos.total_cost + cost) / new_shares

        if is_fractional:
            pos.shares += add_shares
        else:
            pos.quantity += quantity

        self._cash -= cost

    def reduce_position(
        self,
        code: str,
        quantity: int,
        price: float,
        is_fractional: bool,
        shares: int,
    ) -> None:
        """減碼持倉，釋出資金。減到 0 自動刪除持倉。"""
        pos = self._positions.get(code)
        if pos is None:
            raise ValueError(f"未持有 {code}")

        reduce_shares = shares if is_fractional else quantity * 1_000
        if reduce_shares > pos.total_shares:
            raise ValueError(
                f"持股不足：要減 {reduce_shares} 股，但只有 {pos.total_shares} 股"
            )

        proceeds = price * reduce_shares
        self._cash += proceeds

        remaining = pos.total_shares - reduce_shares
        if remaining == 0:
            del self._positions[code]
        else:
            if is_fractional:
                pos.shares -= reduce_shares
            else:
                pos.quantity -= quantity

    def close_position(self, code: str, price: float) -> None:
        """全部平倉，回收資金。"""
        pos = self._positions.get(code)
        if pos is None:
            raise ValueError(f"未持有 {code}")

        self._cash += price * pos.total_shares
        del self._positions[code]

    def update_prices(self, prices: dict[str, float]) -> None:
        """更新持倉的最新市價（用於盤中損益顯示）。"""
        for code, price in prices.items():
            if code in self._positions:
                self._positions[code].current_price = price

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """存檔到 JSON。"""
        data = {
            "initial_capital": self._initial_capital,
            "cash":            self._cash,
            "positions": [
                {
                    "code":          pos.code,
                    "name":          pos.name,
                    "quantity":      pos.quantity,
                    "is_fractional": pos.is_fractional,
                    "shares":        pos.shares,
                    "avg_cost":      pos.avg_cost,
                    "entry_date":    pos.entry_date.isoformat(),
                    "entry_reason":  pos.entry_reason,
                    "current_price": pos.current_price,
                }
                for pos in self._positions.values()
            ],
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(
        cls,
        path: str,
        default_capital: float = _DEFAULT_CAPITAL,
    ) -> "SimulatedPortfolio":
        """從 JSON 載入。檔案不存在時回傳空的 portfolio。"""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return cls(initial_capital=default_capital)

        portfolio = cls(initial_capital=data.get("initial_capital", default_capital))
        portfolio._cash = data.get("cash", default_capital)

        for d in data.get("positions", []):
            portfolio._positions[d["code"]] = Position(
                code=d["code"],
                name=d["name"],
                quantity=d["quantity"],
                is_fractional=d["is_fractional"],
                shares=d["shares"],
                avg_cost=d["avg_cost"],
                entry_date=date.fromisoformat(d["entry_date"]),
                entry_reason=d["entry_reason"],
                current_price=d.get("current_price", 0.0),
            )

        return portfolio

    # ── 內部工具 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_cost(
        price: float, quantity: int, is_fractional: bool, shares: int
    ) -> float:
        if is_fractional:
            return price * shares
        return price * quantity * 1_000

    def repair_to_initial(self) -> None:
        """重置現金為初始資本，清空異常持倉。緊急修復用。"""
        self._cash = self._initial_capital
        self._positions.clear()


# ── Conversion helper ─────────────────────────────────────────────────────────

def positions_to_portfolio(
    positions: list[dict],
    available_cash: float = 0.0,
) -> SimulatedPortfolio:
    """Convert a list of position dicts (from fetch_positions) into SimulatedPortfolio.

    Each dict should have:
        stock_code, name, cost (avg price), current (current price), quantity (張數)
    """
    portfolio = SimulatedPortfolio(initial_capital=available_cash)
    # Set cash directly — positions are already held, not "bought" from this capital
    portfolio._cash = available_cash

    for pos in positions:
        code     = pos["stock_code"]
        name     = pos.get("name", code)
        avg_cost = float(pos.get("cost", 0.0))
        current  = float(pos.get("current", 0.0))
        quantity = int(pos.get("quantity", 0))

        # Directly insert without cash deduction (importing existing positions)
        portfolio._positions[code] = Position(
            code=code,
            name=name,
            quantity=quantity,
            is_fractional=False,
            shares=0,
            avg_cost=avg_cost,
            entry_date=date.today(),
            entry_reason="imported",
            current_price=current,
        )

    return portfolio
