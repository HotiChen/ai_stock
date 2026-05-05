from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import config
from ai_client import call_haiku, call_sonnet

log = logging.getLogger(__name__)
from strategy_tracker import StrategyGoal
from portfolio import SimulatedPortfolio


# ── StrategyPlannerGoal ───────────────���───────────────────────────────────────

@dataclass
class StrategyPlannerGoal:
    """輕量的計劃生成參數，供 build_planner_prompt 使用。
    與 StrategyGoal 並存；當不需要追蹤目標時可直接使用此類別。
    """
    initial_capital:   float
    target_return_pct: float          # 目標報酬 %
    max_drawdown_pct:  float          # 最大容忍回撤 %
    holding_days:      int            # 預計持有天數
    risk_level:        str            # "aggressive" | "moderate" | "conservative"
    stop_loss_pct:     Optional[float] = None  # 停損觸發 % (e.g. 5.0 = -5%)
    max_positions:     Optional[int]   = None  # 最多同時持有幾支
    allow_fractional:  bool            = False  # 是否允許零股


def _extract_json(raw: str) -> dict:
    """Try json.loads first; if that fails, find the outermost {...} block."""
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        return json.loads(m.group())
    raise ValueError("no JSON found")


# ── Data models ───────────────────────────────────────────────────────────────

# 不消耗資金的 action 類型（module-level，避免 dataclass 序列化問題）
_FREE_ACTIONS = frozenset({"hold", "close", "reduce", "switch"})
_BUY_ACTIONS  = frozenset({"open", "add", "buy"})


@dataclass
class StockPick:
    code:                str
    name:                str
    action:              str    # "open"|"add"|"reduce"|"close"|"switch"|"hold"  (舊"buy"向後兼容)
    quantity:            int
    hold_days:           int
    expected_return_pct: float
    reason:              str
    confidence:          int    # 0–10
    key_catalysts:       list[str] = field(default_factory=list)
    entry_logic:         str = ""
    exit_logic:          str = ""
    is_fractional:       bool = False
    shares:              int = 0
    switch_from:         str = ""   # switch action 時要賣掉的股票代碼

    def total_cost(self, price: float) -> float:
        """open/add/buy 消耗資金；hold/close/reduce/switch 資金中性（回傳 0）。"""
        if self.action in _FREE_ACTIONS:
            return 0.0
        if self.is_fractional:
            return self.shares * price
        return self.quantity * 1000 * price


@dataclass
class StrategyPlan:
    plan_type:            str   # "aggressive" | "balanced" | "conservative"
    description:          str
    picks:                list[StockPick]
    capital_deployed_pct: float
    expected_return_pct:  float
    risk_note:            str
    thesis:               str = ""  # 整體策略論述（總體環境 + 選股邏輯）
    macro_context:        str = ""  # 總體環境摘要（美股 / 政策 / 產業趨勢）


@dataclass
class PlanSet:
    aggressive:   StrategyPlan
    balanced:     StrategyPlan
    conservative: StrategyPlan
    generated_at: date


# ── Capital helpers ───────────────────────────────────────────────────────────

def calc_max_lots(price: float, capital: float) -> int:
    """一張 = 1000 股。回傳以 capital 最多能買幾張。"""
    if price <= 0 or capital <= 0:
        return 0
    return int(capital // (price * 1000))


def calc_max_shares(price: float, capital: float) -> int:
    """零股模式：回傳以 capital 最多能買幾股（不限整張）。"""
    if price <= 0 or capital <= 0:
        return 0
    return int(capital // price)


def filter_affordable_candidates(
    candidates: list[dict], capital: float
) -> list[dict]:
    """回傳本金買得起的候選股，加上 max_lots / max_shares / is_fractional_only 欄位。
    - 整張買得起（max_lots > 0）：優先回傳
    - 整張買不起但零股買得起（max_shares > 0）：標記 is_fractional_only=True
    - 兩者都買不起或無報價：排除
    """
    result = []
    for c in candidates:
        price = float(c.get("close", 0) or 0)
        if price <= 0:
            continue
        max_lots  = calc_max_lots(price, capital)
        max_shares = calc_max_shares(price, capital)
        if max_lots > 0:
            result.append({**c, "max_lots": max_lots, "max_shares": max_shares,
                           "is_fractional_only": False})
        elif max_shares > 0:
            result.append({**c, "max_lots": 0, "max_shares": max_shares,
                           "is_fractional_only": True})
    return result


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_positions_section(portfolio: Optional[SimulatedPortfolio]) -> str:
    """持倉區段文字，供 prompt 使用。無持倉回傳空字串。"""
    if portfolio is None:
        return ""
    positions = portfolio.get_positions()
    if not positions:
        return ""

    lines = ["=== 現有持倉（請針對這些持倉給出 hold/add/reduce/close/switch 建議）==="]
    for pos in positions:
        pnl     = pos.unrealized_pnl(pos.current_price) if pos.current_price > 0 else 0.0
        pnl_pct = pos.unrealized_pnl_pct(pos.current_price) if pos.current_price > 0 else 0.0
        hold_tag = "整張" if not pos.is_fractional else "零股"
        qty_str  = f"{pos.quantity}張" if not pos.is_fractional else f"{pos.shares}股"
        lines.append(
            f"- {pos.code} {pos.name}　{qty_str}（{hold_tag}）　"
            f"成本 {pos.avg_cost:,.1f}/股　"
            f"現價 {pos.current_price:,.1f}/股　"
            f"未實現損益 {pnl:+,.0f} TWD（{pnl_pct:+.1f}%）\n"
            f"  進場理由：{pos.entry_reason}"
        )
    lines.append(f"\n可用現金：{portfolio.get_available_capital():,.0f} TWD")
    lines.append(
        "（新建倉 open/加碼 add 只能使用上方可用現金，"
        "持有 hold/減碼 reduce/平倉 close/換股 switch 不需額外資金）"
    )
    return "\n".join(lines)


def _fractional_rule(goal) -> str:
    """依 allow_fractional 決定零股買法的 prompt 說明。"""
    allow = getattr(goal, "allow_fractional", False)
    if allow:
        return (
            "- **零股買法**：標記【零股】的股票用 is_fractional = true，"
            "shares = 股數（不超過上方最多可買股數），quantity = 0\n"
            "- 整張買不起時，**請優先考慮零股**（is_fractional=true）而非直接跳過"
        )
    else:
        return (
            "- **不使用零股**：所有 picks 的 is_fractional 必須為 false，shares 必須為 0\n"
            "- 整張買不起的股票請直接跳過，不要用零股方式買入"
        )


def _goal_constraint_section(goal) -> str:
    """從 goal 抽取停損/最大持倉/零股等限制，生成 prompt 段落。"""
    lines = []

    # 支援 StrategyGoal (stop_loss_pct, max_positions, allow_fractional) 及 StrategyPlannerGoal
    stop_loss = getattr(goal, "stop_loss_pct", None)
    max_pos   = getattr(goal, "max_positions", None)
    allow_frac = getattr(goal, "allow_fractional", False)

    if stop_loss is not None:
        lines.append(f"- **停損規則**：每筆持倉虧損超過 {stop_loss:.1f}% 時必須停損出場")
    if max_pos is not None:
        lines.append(f"- **最多持倉**：同時持有不超過 {max_pos} 支股票")
    if allow_frac:
        lines.append("- **允許零股**：整張買不起時，請優先考慮零股（is_fractional=true）")
    else:
        lines.append("- **不使用零股**：僅考慮整張買賣（is_fractional=false）")

    allow_day_trade = getattr(goal, "allow_day_trade", False)
    if allow_day_trade:
        lines.append("- **允許當沖**：可建議當日沖銷操作，同一天買進賣出即可，不需隔夜持倉")
    else:
        lines.append("- **不做當沖**：所有持倉須隔夜，不建議當日沖銷")

    return "\n".join(lines) if lines else ""


def build_planner_prompt(
    goal,
    current_value: float,
    candidates: list[dict],
    market_summary: str = "",
    portfolio: Optional[SimulatedPortfolio] = None,
    weekly_hint: list[str] | None = None,
) -> str:
    # 支援 StrategyGoal 和 StrategyPlannerGoal 兩種傳入方式
    if hasattr(goal, "end_date"):
        days_left = (goal.end_date - date.today()).days
    else:
        days_left = getattr(goal, "holding_days", 5)

    if hasattr(goal, "target_value"):
        target_pnl = goal.target_value - current_value
        target_str = f"{goal.target_value:,.0f} 元（{goal.target_multiplier}x）"
    else:
        target_pct = getattr(goal, "target_return_pct", 10.0)
        target_pnl = current_value * target_pct / 100
        target_str = f"報酬率 {target_pct:.1f}%（目標獲利 {target_pnl:,.0f} 元）"

    approach = getattr(goal, "approach", None) or getattr(goal, "risk_level", "moderate")

    stocks_text = ""
    for c in candidates:
        price     = float(c.get("close", 0) or 0)
        if price <= 0:
            continue
        analysis  = c.get("analysis") or "無分析"
        max_lots  = c.get("max_lots", 0)
        max_shares = c.get("max_shares", calc_max_shares(price, current_value))
        frac_only = c.get("is_fractional_only", False)
        lot_cost  = price * 1000

        if frac_only:
            buy_tag = f"【零股】最多可買 {max_shares} 股（每股 {price:.1f} 元，整張需 {lot_cost:,.0f} 元買不起）"
        else:
            buy_tag = f"最多可買 {max_lots} 張（整張）或 {max_shares} 股（零股）"

        stocks_text += (
            f"- {c['code']} {c.get('name', '')}　"
            f"現價 {price:,.1f}　{buy_tag}　"
            f"漲跌 {c.get('change_rate', 0):+.1f}%　"
            f"分析：{analysis}\n"
        )
    if not stocks_text:
        stocks_text = f"（目前無本金 {current_value:,.0f} 元以內可買的候選股票）\n"

    market_section     = f"\n=== 總體環境 ===\n{market_summary}\n" if market_summary else ""
    positions_section  = _build_positions_section(portfolio)
    available_capital  = (
        portfolio.get_available_capital() if portfolio is not None
        else current_value
    )
    constraint_section = _goal_constraint_section(goal)
    constraint_block   = (
        f"\n=== 使用者自訂限制 ===\n{constraint_section}\n"
        if constraint_section else ""
    )
    weekly_hint_block = ""
    if weekly_hint:
        hints = "\n".join(f"• {h}" for h in weekly_hint)
        weekly_hint_block = f"\n=== 上週 AI 複盤建議（請納入本週策略參考）===\n{hints}\n"

    return f"""你是一位有野心的台股投資策略師，擅長把總體環境、產業趨勢、個股催化劑整合成具體可執行的投資計劃。

=== 投資目標 ===
初始本金：{goal.initial_capital:,.0f} 元
目前資產：{current_value:,.0f} 元
目標：{target_str}
還需獲利：{target_pnl:,.0f} 元
剩餘天數：{days_left} 天
策略方向：{approach}
{market_section}
{positions_section}
{weekly_hint_block}
{constraint_block}
=== AI 分析過的候選股票（可用現金：{available_capital:,.0f} 元）===
{stocks_text}
=== 任務 ===
生成三套計劃（aggressive 衝刺 / balanced 均衡 / conservative 保守）。

**資金限制（強制遵守）：**
- 台灣股票 1 張 = 1000 股，股價 × 1000 = 一張成本
- **只能從上方候選清單中選股，絕對不可以自行編造股票代碼或選清單以外的股票**
- 所有計劃的所有 picks 花費加總不得超過本金 {current_value:,.0f} 元
- 若候選清單為空，則三套計劃的 picks 都給空陣列 []
- **整張買法**：quantity = 張數，is_fractional = false，shares = 0
{_fractional_rule(goal)}

**每套計劃都必須寫出清楚的投資論述，包含：**
- 為什麼現在進場？（總體環境、美股影響、政策利多）
- 為什麼選這支股票？（訂單能見度、法人動向、產業趨勢）
- 何時出場？（目標價邏輯、停損條件）

只回答 JSON，不要其他文字：
{{
  "aggressive": {{
    "description": "一句話說明特色",
    "thesis": "詳細整體論述：總體環境如何、為何現在是好時機、選股邏輯是什麼（3到5句）",
    "macro_context": "總體環境摘要：美股走勢、Fed 政策、台灣/美國相關政策、產業趨勢",
    "capital_deployed_pct": 0到1,
    "expected_return_pct": 整體預期報酬百分比,
    "risk_note": "主要風險提示",
    "picks": [
      {{
        "code": "股票代碼",
        "name": "股票名稱",
        "action": "buy",
        "quantity": 張數整數,
        "hold_days": 持有天數,
        "expected_return_pct": 預期報酬百分比,
        "reason": "一句話核心理由",
        "confidence": 0到10,
        "key_catalysts": ["催化劑1（例：法人連買3日）", "催化劑2（例：AI伺服器訂單能見度至2027）", "催化劑3"],
        "entry_logic": "進場邏輯：技術面在哪個位置進？基本面支撐是什麼？",
        "exit_logic": "出場邏輯：目標價是多少？跌破哪裡停損？持有幾天後重新評估？"
      }}
    ]
  }},
  "balanced": {{ 同上格式 }},
  "conservative": {{ 同上格式 }}
}}"""


# ── Individual plan prompts (one per strategy type) ───────────────────────────

_SELECTION_FRAMEWORK = """\
你必須從五個維度評估每一支候選股票，每個維度都有分數，加總後才能決定是否選入。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
維度一｜宏觀 & 題材（Macro & Theme）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 費城半導體指數（SOX）走勢：SOX 上漲 → 台股電子股加分；SOX 下跌 → 扣分
• 美股龍頭聯動：NVDA / TSMC ADR 強勢 → 相關族群加分
• 資金輪動方向：目前資金流入哪個產業？（半導體、航運、重電、金融…）選流入的，不選流出的
• 政策題材：是否受惠政府預算（綠能/國防/生技）或季末作帳行情？

維度二｜基本面（Fundamental）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 月營收 YoY：連續 3 個月 > 10% 或創歷史新高 → 強力加分
• 獲利三率（毛利率、營業利益率、淨利率）：三率同步上升 → 加分；衰退 → 扣分
• ROE > 15%：公司有效運用資金的證明
• 自由現金流為正：確保公司不是空燒錢
（注意：短線操作時基本面為輔助篩選，排除地雷股用，不是主要評分）

維度三｜技術面（Technical）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 均線多頭排列：股價在 MA5 > MA20 > MA60 之上，且均線向上擴張 → 加分
• KD/RSI 背離：股價創低但指標不創低 → 反轉信號，加分
• 量價關係：
  - 縮量回測均線（成交量萎縮 + 股價守住）→ 進場時機
  - 帶量突破壓力（成交量 ≥ 前 5 日均量 × 1.5）→ 強烈加分
• 位階：避開布林通道上軌（BBU）之外的過熱區；尋找「底部起漲」或「盤整結束」

維度四｜籌碼面（Fund Flow）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 投信連買 ≥ 3 天：中短期強勢波段力量，高度關注
• 外資動向：持續買超權值股 → 市場偏多；外資大賣 → 市場警戒
• 大戶持股比例（400張/1000張以上）：比例上升 → 籌碼集中，主力在吸貨
• 融資餘額下降 + 股價上漲：散戶停損、主力吃貨的典型信號，強力加分

維度五｜流動性 & 風險控制（Liquidity & Risk）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 流動性門檻：日成交張數 > 1,000 張（賣不掉的不買）；股本 > 10 億（避免人為操控）
• ATR 波動率：波動越大 → 進場位階需更保守，部位需更小
• 止損規則：
  - 移動止損（Trailing Stop）：從最高點回撤 7% 出場
  - 硬止損（Hard Stop）：跌破 20MA 立即停損
• 風險報酬比：預期獲利 ÷ 最大虧損 ≥ 2（虧 1 才要有機會賺 2）
"""

_STYLE_CONFIGS = {
    "aggressive": {
        "label": "衝刺版（aggressive）",
        "style": "積極集中衝刺高報酬",
        "instructions": (
            "- 集中持股，選 1–2 支最有把握的\n"
            "- 資金部署 80–100%\n"
            "- 可接受較高波動，以高報酬為優先\n"
            "- 若本金不夠整張，優先考慮零股（is_fractional=true）"
        ),
        "selection_criteria": (
            "**衝刺版選股門檻（五維度加權）：**\n\n"
            "【維度一：宏觀 & 題材】權重最高\n"
            "- 必須處於資金流入的產業，不選資金在撤退的產業\n"
            "- SOX 或 NVDA 強勢時，半導體/AI 族群優先\n"
            "- 有明確短期催化劑（法說、財報、重大訂單、政策宣布）加 3 分\n\n"
            "【維度二：基本面】只做排雷，不做主要評分\n"
            "- 月營收衰退超過 20% 或三率連續下滑 → 直接排除\n"
            "- 其餘基本面不作硬性門檻（短線以題材和籌碼為主）\n\n"
            "【維度三：技術面】重點看突破信號\n"
            "- 必須有帶量突破（成交量 ≥ 前 5 日均量 × 1.5）或均線多頭排列\n"
            "- 位於布林通道上軌（BBU）外的過熱股不追，等回測再進\n"
            "- KD 或 RSI 背離出現 → 強力加分\n\n"
            "【維度四：籌碼面】核心評分依據\n"
            "- 投信連買 ≥ 3 天 → 加 3 分\n"
            "- 大戶 400 張以上持股比例上升 → 加 2 分\n"
            "- 融資餘額下降 + 股價上漲（主力吃貨信號）→ 加 3 分\n"
            "- 外資大量賣超 → 扣 2 分\n\n"
            "【維度五：流動性 & 風險】基本門檻\n"
            "- 日成交量 > 1,000 張（流動性不足不買）\n"
            "- 股本 > 10 億（除非有極強題材）\n"
            "- 止損設在進場價下方 7%（Trailing Stop），跌破 20MA 硬出\n"
            "- 本金效率優先：計算零股 vs 整張哪個報酬率更高再決定\n\n"
            "**最終決策：**\n"
            "- 五維度總分最高的 1–2 支入選\n"
            "- 信心分數 < 7 者直接排除（信心分數需反映以上五維度的綜合評估）"
        ),
        "keywords": "衝刺 積極 集中",
    },
    "balanced": {
        "label": "均衡版（balanced）",
        "style": "均衡分散穩健成長",
        "instructions": (
            "- 分散 2–3 支，來自不同產業\n"
            "- 資金部署 50–70%\n"
            "- 整張優先，零股作為補充或試倉\n"
            "- 平衡風險，不過度集中單一個股"
        ),
        "selection_criteria": (
            "**均衡版選股評分（五維度各佔權重，總分高者入選）：**\n\n"
            "【維度一：宏觀 & 題材】20%\n"
            "- 順應當前資金輪動方向，但不強求最熱題材\n"
            "- SOX / 外資動向作為大盤多空過濾：大盤明顯空頭時縮手\n"
            "- 受惠政策（綠能/國防/生技）或季末作帳行情 → +2 分\n\n"
            "【維度二：基本面】30%（比衝刺版更重視）\n"
            "- 月營收 YoY > 10%（連續 2 個月即可）→ +3 分\n"
            "- 毛利率、營業利益率同步上升 → +2 分\n"
            "- ROE > 15% → +2 分；自由現金流為正 → +1 分\n"
            "- 月營收年減 > 10% → 直接排除\n\n"
            "【維度三：技術面】25%\n"
            "- 股價在 MA20 之上，均線多頭排列 → +3 分\n"
            "- 縮量回測均線支撐（量縮守住）→ 最佳進場點，+3 分\n"
            "- 帶量突破壓力 → +2 分\n"
            "- 位於 BBU 外（過熱）→ 不追，等回測\n"
            "- KD/RSI 背離 → +2 分\n\n"
            "【維度四：籌碼面】15%\n"
            "- 投信連買 ≥ 3 天 → +3 分\n"
            "- 大戶持股比例上升 → +2 分\n"
            "- 融資餘額下降 + 股價上漲 → +2 分\n\n"
            "【維度五：流動性 & 風險】10%（硬門檻）\n"
            "- 日成交量 > 1,000 張（不達標直接排除）\n"
            "- 股本 > 10 億（不達標直接排除）\n"
            "- 止損：跌破 20MA 硬出；最大虧損設 5%\n"
            "- 風險報酬比 ≥ 1:2\n\n"
            "**分散原則：**\n"
            "- 不同產業各選一支（半導體 + 金融、科技 + 傳產…）\n"
            "- 單支佔資金不超過 40%\n"
            "- 整張優先；整張買不起才用零股，零股可做試倉用\n\n"
            "**最終決策：**\n"
            "- 加總五維度分數，選最高的 2–3 支，且必須來自不同產業\n"
            "- 信心分數 < 6 者排除"
        ),
        "keywords": "均衡 分散 平衡",
    },
    "conservative": {
        "label": "保守版（conservative）",
        "style": "保守穩健保本優先",
        "instructions": (
            "- 只選確定性最高、風險最低的標的\n"
            "- 資金部署 30–50%\n"
            "- 保本優先，寧可少賺不要虧損\n"
            "- 停損條件明確，不確定就空手"
        ),
        "selection_criteria": (
            "**保守版選股門檻（五維度全部達標才能入選，有一項不過直接排除）：**\n\n"
            "【維度一：宏觀 & 題材】必要條件\n"
            "- 大盤趨勢必須偏多（SOX 未破季線、外資非大量撤退）\n"
            "- 不追熱門題材的頂部；只選「趨勢已確立」的產業，不賭新題材\n"
            "- 大盤疑慮未解或有重大地緣政治風險 → 空手不進場\n\n"
            "【維度二：基本面】最嚴格門檻（必須全過）\n"
            "- 月營收 YoY > 10%，連續 3 個月 → 必要條件\n"
            "- 毛利率、營業利益率、淨利率三率同步上升或維持高水位 → 必要條件\n"
            "- ROE > 15% → 必要條件\n"
            "- 自由現金流為正 → 必要條件\n"
            "- 任何一項不符合 → 直接排除（不論其他面向多好）\n\n"
            "【維度三：技術面】只買「低風險進場點」\n"
            "- 股價必須在 MA5 > MA20 > MA60 多頭排列之上 → 必要條件\n"
            "- 必須是縮量回測均線的支撐點（不是高點追進）→ 最佳進場\n"
            "- 帶量突破後的第一次縮量回測 → 可接受\n"
            "- 布林通道上軌（BBU）外 → 絕對不追\n"
            "- KD 或 RSI 背離 → 加分但非必要\n\n"
            "【維度四：籌碼面】必須有法人支撐\n"
            "- 投信連買 ≥ 3 天 或 外資持續買超 → 必要條件之一\n"
            "- 大戶持股比例上升 → 加分\n"
            "- 融資餘額持續增加（散戶在追）→ 警戒信號，謹慎\n"
            "- 外資大量賣超 → 直接排除\n\n"
            "【維度五：流動性 & 風險】最嚴格（全部硬門檻）\n"
            "- 日成交張數 > 1,000 張 → 必要條件\n"
            "- 股本 > 10 億 → 必要條件\n"
            "- 波動率（ATR）：單日波動超過 5% 的高波動股 → 排除\n"
            "- 止損：硬止損跌破 20MA 立即出；Trailing Stop 從最高點回撤 7%\n"
            "- 風險報酬比 ≥ 1:2（最大虧損 3%，才進場）\n"
            "- 先問「最壞情況下虧多少」，超過 3% 就不買\n\n"
            "**零股試倉策略：**\n"
            "- 對高確信度但整張太貴的股票，可先用零股試倉（買 10–30% 預計部位）\n"
            "- 確認方向正確後再決定是否加碼\n\n"
            "**最終決策：**\n"
            "- 只選信心分數 ≥ 8 的標的（反映五維度全通過的高確定性）\n"
            "- 寧可空手（picks 為空），也不買五維度有缺口的股票"
        ),
        "keywords": "保守 穩健 保本",
    },
}


def build_plan_prompt(
    plan_type: str,
    goal: StrategyGoal,
    capital: float,
    candidates: list[dict],
    market_summary: str = "",
    portfolio: Optional[SimulatedPortfolio] = None,
) -> str:
    """生成單一策略類型的獨立 prompt。"""
    cfg = _STYLE_CONFIGS.get(plan_type, _STYLE_CONFIGS["balanced"])
    days_left  = (goal.end_date - date.today()).days
    target_pnl = goal.target_value - capital
    positions_section = _build_positions_section(portfolio)
    available_capital = (
        portfolio.get_available_capital() if portfolio is not None
        else capital
    )

    stocks_text = ""
    for c in candidates:
        price     = float(c.get("close", 0) or 0)
        max_lots  = c.get("max_lots") if "max_lots" in c else calc_max_lots(price, capital)
        max_sh    = calc_max_shares(price, capital)
        lot_cost  = price * 1000
        if max_lots > 0:
            tag = f"最多可買 {max_lots} 張（整張）｜或最多 {max_sh} 股（零股）"
        else:
            tag = f"整張買不起（需 {lot_cost:,.0f} 元）｜零股最多 {max_sh} 股"
        stocks_text += (
            f"- {c['code']} {c.get('name', '')}　現價 {price:,.1f}　{tag}　"
            f"漲跌 {c.get('change_rate', 0):+.1f}%　"
            f"分析：{c.get('analysis') or '無分析'}\n"
        )
    if not stocks_text:
        stocks_text = f"（無候選股）\n"

    market_section = f"\n=== 總體環境 ===\n{market_summary}\n" if market_summary else ""

    return f"""你是一位台股投資策略師，現在只需要生成【{cfg['label']}】這一套計劃。
風格定位：{cfg['style']}

=== 投資目標 ===
初始本金：{goal.initial_capital:,.0f} 元
目前資金：{capital:,.0f} 元
目標：{goal.target_value:,.0f} 元（{goal.target_multiplier}x）
還需獲利：{target_pnl:,.0f} 元
剩餘天數：{days_left} 天
策略方向：{goal.approach}
{market_section}
{positions_section}

=== 五維度選股框架（適用所有策略）===
{_SELECTION_FRAMEWORK}
=== 候選股票（含零股資訊）===
{stocks_text}
=== 此策略的選股標準（{cfg['label']}專屬門檻）===
{cfg['selection_criteria']}

=== 此策略的操作原則 ===
{cfg['instructions']}

=== 零股說明 ===
- 台灣零股 1–999 股，盤後 13:40–14:30 交易
- 若整張買不起，可用零股方式進場（is_fractional=true，填 shares 數量）
- 整張（quantity）與零股（shares）擇一，不能同時填

=== 資金與預算限制（絕對強制遵守，違規將導致計畫爆倉破產）===
1. 此計畫的【可用總資金】上限為：{capital:,.0f} 元。
2. 系統將會根據你的 picks 清單「一次性」買進所有你推薦的股票。
3. 因此，你推薦的所有股票其「購買成本總和（各股 現價×1000×數量，或 現價×零股股數 的加總）」絕對不可以超過 {capital:,.0f} 元！
4. 若總成本超出 {capital:,.0f} 元，系統將直接拒絕下單。

=== 回傳格式（只回 JSON，不要其他文字）===
{{
  "description": "一句話說明此計劃特色",
  "thesis": "整體論述：為何現在進場、選股邏輯（3–5句）",
  "macro_context": "總體環境摘要",
  "capital_deployed_pct": 0到1,
  "expected_return_pct": 整體預期報酬百分比,
  "risk_note": "主要風險提示",
  "picks": [
    {{
      "code": "股票代碼",
      "name": "股票名稱",
      "code": "股票代碼",
      "name": "股票名稱",
      "action": "open（新建倉）| add（加碼）| reduce（減碼）| close（平倉）| switch（換股）| hold（維持不動）",
      "switch_from": "換股時填被賣掉的舊股代碼，其他 action 填空字串",
      "quantity": 張數（整張時填，零股時填0）,
      "hold_days": 持有天數,
      "expected_return_pct": 預期報酬百分比,
      "reason": "核心理由",
      "confidence": 0到10,
      "is_fractional": false或true,
      "shares": 零股股數（整張時填0）,
      "key_catalysts": ["催化劑1", "催化劑2"],
      "entry_logic": "進場邏輯",
      "exit_logic": "出場邏輯"
    }}
  ]
}}"""


def generate_plan(
    plan_type: str,
    goal: StrategyGoal,
    capital: float,
    candidates: list[dict],
    market_summary: str = "",
) -> Optional["StrategyPlan"]:
    """呼叫 AI 生成單一策略計劃。失敗回傳 None。"""
    prompt = build_plan_prompt(plan_type, goal, capital, candidates, market_summary)
    try:
        raw  = call_sonnet(prompt)
        data = _extract_json(raw)
        return _parse_one(data, plan_type)
    except Exception:
        return None


# ── Parser ────────────────────────────────────────────────────────────────────

def _parse_picks(raw_picks: list[dict]) -> list[StockPick]:
    picks = []
    for p in raw_picks:
        try:
            is_frac = bool(p.get("is_fractional", False))
            shares  = max(0, int(p.get("shares", 0)))
            qty_raw = int(p.get("quantity", 1))
            quantity = max(0, qty_raw) if is_frac else max(1, qty_raw)
            picks.append(StockPick(
                code=str(p["code"]),
                name=str(p.get("name", p["code"])),
                action=p.get("action", "open"),
                quantity=quantity,
                hold_days=max(1, int(p.get("hold_days", 1))),
                expected_return_pct=float(p.get("expected_return_pct", 0.0)),
                reason=str(p.get("reason", "")),
                key_catalysts=p.get("key_catalysts", []),
                entry_logic=str(p.get("entry_logic", "")),
                exit_logic=str(p.get("exit_logic", "")),
                confidence=max(0, min(10, int(p.get("confidence", 5)))),
                is_fractional=is_frac,
                shares=shares,
                switch_from=str(p.get("switch_from", "")),
            ))
        except Exception:
            continue
    return picks


def _parse_one(data: dict, plan_type: str) -> Optional[StrategyPlan]:
    try:
        return StrategyPlan(
            plan_type=plan_type,
            description=str(data.get("description", "")),
            picks=_parse_picks(data.get("picks", [])),
            capital_deployed_pct=float(data.get("capital_deployed_pct", 0.5)),
            expected_return_pct=float(data.get("expected_return_pct", 0.0)),
            risk_note=str(data.get("risk_note", "")),
            thesis=str(data.get("thesis", "")),
            macro_context=str(data.get("macro_context", "")),
        )
    except Exception:
        return None


def parse_plan_response(raw: str) -> Optional[PlanSet]:
    try:
        data = _extract_json(raw)
        agg  = _parse_one(data["aggressive"],   "aggressive")
        bal  = _parse_one(data["balanced"],     "balanced")
        con  = _parse_one(data["conservative"], "conservative")
        if agg is None or bal is None or con is None:
            return None
        return PlanSet(
            aggressive=agg,
            balanced=bal,
            conservative=con,
            generated_at=date.today(),
        )
    except Exception:
        return None


# ── Main entry ────────────────────────────────────────────────────────────────

def _clamp_quantities(plan_set: PlanSet, candidates: list[dict], capital: float) -> PlanSet:
    """確保計劃內所有 picks 的總花費不超過本金。
    - 整張模式：max_lots=0 → 直接排除該 pick
    - 零股模式：調整 shares 不超過剩餘本金
    - 最後按信心分數由高到低累加，超出本金的 picks 一律移除
    """
    price_map = {c["code"]: float(c.get("close", 0) or 0) for c in candidates}

    def _pick_cost(pick: StockPick, price: float) -> float:
        if pick.is_fractional:
            return (pick.shares or 0) * price
        return pick.quantity * 1000 * price

    # 不消耗資金的 action 類型
    _FREE_ACTIONS = frozenset({"hold", "close", "reduce", "switch"})
    # 向後兼容舊 "buy" action
    _BUY_ACTIONS  = frozenset({"open", "add", "buy"})

    def _clamp_plan(plan: StrategyPlan) -> StrategyPlan:
        # 第一步：過濾 + 調整個別數量
        free_picks: list[StockPick] = []    # hold/close/reduce/switch — 不消耗資金
        candidates_ok: list[StockPick] = [] # open/add/buy — 需要資金

        for pick in plan.picks:
            # 不消耗資金的 action：不需要價格驗證，直接保留
            if pick.action in _FREE_ACTIONS:
                free_picks.append(pick)
                continue

            price = price_map.get(pick.code, 0.0)
            if price <= 0:
                continue   # 沒有真實報價 → 跳過（可能是 AI 幻覺代碼）
            if pick.is_fractional:
                max_sh  = calc_max_shares(price, capital)
                new_sh  = min(pick.shares or 1, max_sh) if max_sh > 0 else 0
                if new_sh <= 0:
                    continue   # 連 1 股都買不起，跳過
                candidates_ok.append(StockPick(
                    code=pick.code, name=pick.name, action=pick.action,
                    quantity=pick.quantity, hold_days=pick.hold_days,
                    expected_return_pct=pick.expected_return_pct,
                    reason=pick.reason, confidence=pick.confidence,
                    key_catalysts=pick.key_catalysts,
                    entry_logic=pick.entry_logic, exit_logic=pick.exit_logic,
                    is_fractional=True, shares=new_sh,
                    switch_from=pick.switch_from,
                ))
            else:
                max_lots = calc_max_lots(price, capital)
                if max_lots <= 0:
                    continue   # 連 1 張都買不起，跳過
                new_qty = min(pick.quantity, max_lots)
                candidates_ok.append(StockPick(
                    code=pick.code, name=pick.name, action=pick.action,
                    quantity=new_qty, hold_days=pick.hold_days,
                    expected_return_pct=pick.expected_return_pct,
                    reason=pick.reason, confidence=pick.confidence,
                    key_catalysts=pick.key_catalysts,
                    entry_logic=pick.entry_logic, exit_logic=pick.exit_logic,
                    is_fractional=False, shares=pick.shares,
                    switch_from=pick.switch_from,
                ))

        # 第二步：按信心分數排序，累加直到總花費超出本金
        candidates_ok.sort(key=lambda p: p.confidence, reverse=True)
        final: list[StockPick] = list(free_picks)   # free picks 永遠保留
        remaining = capital
        for pick in candidates_ok:
            price = price_map.get(pick.code, 0.0)
            cost  = _pick_cost(pick, price) if price > 0 else 0.0
            if cost > remaining:
                continue   # 加了這支就超出本金，跳過
            final.append(pick)
            remaining -= cost

        total_cost = capital - remaining
        deployed_pct = (total_cost / capital) if capital > 0 else 0.0

        return StrategyPlan(
            plan_type=plan.plan_type, description=plan.description,
            picks=final, capital_deployed_pct=deployed_pct,
            expected_return_pct=plan.expected_return_pct, risk_note=plan.risk_note,
            thesis=plan.thesis, macro_context=plan.macro_context,
        )

    return PlanSet(
        aggressive=_clamp_plan(plan_set.aggressive),
        balanced=_clamp_plan(plan_set.balanced),
        conservative=_clamp_plan(plan_set.conservative),
        generated_at=plan_set.generated_at,
    )


def generate_strategy_plans(
    goal: StrategyGoal,
    current_value: float,
    candidates: list[dict],
    portfolio: Optional[SimulatedPortfolio] = None,
    weekly_hint: list[str] | None = None,
) -> Optional[PlanSet]:
    # 若傳入 portfolio，以實際可用現金作為資金上限
    effective_capital = (
        portfolio.get_available_capital() if portfolio is not None else current_value
    )
    # 只把有真實報價且本金買得起的股票傳給 AI，防止 AI 選買不起的股票
    affordable = filter_affordable_candidates(candidates, effective_capital)
    prompt = build_planner_prompt(
        goal, current_value, affordable,
        portfolio=portfolio, weekly_hint=weekly_hint,
    )
    try:
        raw = call_sonnet(prompt)
        if not raw:
            log.error("generate_strategy_plans: call_sonnet returned empty — check ANTHROPIC_API_KEY")
            return None
        plan_set = parse_plan_response(raw)
        if plan_set is None:
            log.error("generate_strategy_plans: parse_plan_response failed, raw[:200]=%s", raw[:200])
            return None
        # 最後再 clamp 一次，防止 AI 忽視上限
        return _clamp_quantities(plan_set, affordable, effective_capital)
    except Exception as e:
        log.error("generate_strategy_plans exception: %s", e)
        return None
