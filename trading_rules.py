from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config
from ai_trader import sanitize_decision
from chat_agent import ChatMessage, format_messages_for_ollama
from news_agent import call_ollama, extract_json

_DEFAULT_PATH = "data/trading_rules.json"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class TradingRules:
    max_trades_per_day:  int   = 1      # 每天最多幾次買 / 幾次賣
    max_position_pct:    float = 0.70   # 單筆最多用本金的幾 %
    stop_loss_pct:       float = 5.0    # 跌幾 % 認賠 (用於 auto_trader 停損)
    take_profit_pct:     float = 15.0   # 漲幾 % 獲利了結
    min_confidence:      int   = 6      # AI 信心分數低於此不執行


@dataclass
class ValidationResult:
    allowed:           bool
    reason:            str
    adjusted_quantity: int | None = None


# ── Persistence ───────────────────────────────────────────────────────────────

def save_rules(rules: TradingRules, path: str = _DEFAULT_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(rules), f, ensure_ascii=False, indent=2)


def load_rules(path: str = _DEFAULT_PATH) -> TradingRules:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        defaults = asdict(TradingRules())
        defaults.update(data)
        return TradingRules(**{k: defaults[k] for k in asdict(TradingRules())})
    except Exception:
        return TradingRules()


# ── Quantity helper ───────────────────────────────────────────────────────────

def calc_max_quantity(cash: float, price: float, max_pct: float) -> int:
    """Return max lots (張=1000股) buyable within cash * max_pct."""
    if price <= 0:
        return 0
    usable = cash * max_pct
    return int(usable // (price * 1000))


# ── Validation ────────────────────────────────────────────────────────────────

def validate_decision(
    decision: dict,
    rules: TradingRules,
    cash: float,
    current_price: float | None,
    buys_today: int,
    sells_today: int,
) -> ValidationResult:
    action = decision.get("action", "hold")

    # hold 永遠放行
    if action == "hold":
        return ValidationResult(allowed=True, reason="持有，無需驗證")

    # 信心分數檢查
    confidence = decision.get("confidence", 0)
    if confidence < rules.min_confidence:
        return ValidationResult(
            allowed=False,
            reason=f"AI 信心分數 {confidence}/10 低於門檻 {rules.min_confidence}/10，不執行",
        )

    # 每日交易次數
    if action == "buy":
        if buys_today >= rules.max_trades_per_day:
            return ValidationResult(
                allowed=False,
                reason=f"今日買入次數已達上限（{rules.max_trades_per_day} 次）",
            )
    elif action == "sell":
        if sells_today >= rules.max_trades_per_day:
            return ValidationResult(
                allowed=False,
                reason=f"今日賣出次數已達上限（{rules.max_trades_per_day} 次）",
            )

    # 買入時的資金與倉位檢查
    if action == "buy":
        if current_price is None:
            return ValidationResult(allowed=False, reason="無法取得現價，無法驗證資金是否足夠")

        requested_qty = decision.get("quantity", 1)
        max_qty = calc_max_quantity(cash, current_price, rules.max_position_pct)

        # 完全買不起（連 1 張都不行）
        if cash < current_price * 1000:
            return ValidationResult(
                allowed=False,
                reason=f"可用現金 {cash:,.0f} 元不足以買入任何一張（需 {current_price*1000:,.0f} 元）",
            )

        # position cap 後取得可買量（至少保留 1 張）
        max_qty = max(max_qty, 1)

        if requested_qty > max_qty:
            return ValidationResult(
                allowed=True,
                reason=f"請求 {requested_qty} 張超過倉位上限，自動調整為 {max_qty} 張",
                adjusted_quantity=max_qty,
            )

    return ValidationResult(allowed=True, reason="通過所有規則檢查")


# ── Chat → Strategy ───────────────────────────────────────────────────────────

def _build_summary_prompt(messages: list[ChatMessage]) -> str:
    if not messages:
        return ""
    dialogue = "\n".join(
        f"[{m.role.upper()}] {m.content}"
        for m in messages
        if m.role in ("user", "assistant")
    )
    return f"""以下是投資人與 AI 顧問的完整對話記錄，請根據對話內容總結出最終的投資決策。

=== 對話記錄 ===
{dialogue}

=== 任務 ===
從以上對話中提取最終決策意圖，只回答 JSON，不要其他文字：
{{"action": "buy/sell/hold", "stock_code": "股票代碼或空字串", "quantity": 張數整數, "reason": "繁體中文一句話說明", "confidence": 0到10}}

如果對話中沒有明確的買賣決定，action 填 "hold"。"""


def summarize_chat_to_strategy(messages: list[ChatMessage]) -> dict:
    """Ask AI to summarize chat discussion into a concrete trading decision."""
    if not messages:
        return sanitize_decision({"action": "hold", "reason": "無對話記錄", "confidence": 0})

    prompt = _build_summary_prompt(messages)
    try:
        raw = call_ollama(config.DECISION_MODEL, prompt, timeout=60)
        result = extract_json(raw)
        if result.get("action") in ("buy", "sell", "hold"):
            return sanitize_decision(result)
    except Exception:
        pass

    return sanitize_decision({"action": "hold", "reason": "AI 摘要失敗，預設持有", "confidence": 0})
