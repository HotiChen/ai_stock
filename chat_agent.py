from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import requests

import config
from ai_client import call_sonnet


@dataclass
class ChatMessage:
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat()[:19])


def build_trading_advisor_prompt(
    positions: list[dict] | None = None,
    ai_log: dict | None = None,
) -> str:
    if not positions:
        pos_text = "目前無持倉"
    else:
        lines = []
        for p in positions:
            lines.append(
                f"- {p.get('name', p.get('stock_code', '?'))}（{p.get('stock_code', '?')}）："
                f"持有 {p.get('quantity', 0)} 股，"
                f"成本 {p.get('cost', 0):.2f}，現價 {p.get('current', 0):.2f}"
            )
        pos_text = "\n".join(lines)

    last_action = "尚無記錄"
    if ai_log and "decision" in ai_log:
        d = ai_log["decision"]
        last_action = (
            f"{d.get('action', '?')}　"
            f"信心 {d.get('confidence', 0)}/10　"
            f"理由：{d.get('reason', '')}"
        )

    return f"""你是一位專業的台股投資顧問 AI，熟悉台灣股市規則和各種投資策略。

## 投資人目前狀況
預算：{config.BUDGET:.0f} 台幣
持倉：
{pos_text}
最近 AI 決策：{last_action}

## 你可以協助
- 分析個股技術面（均線、RSI、MACD、布林通道）
- 解釋各種投資策略的原理與應用
- 提供倉位管理與風險控管建議
- 討論進出場時機

## 可參考的策略框架
- **技術分析**：MA5/MA20/MA60 均線交叉、RSI 超買（>70）超賣（<30）、MACD 黃金/死亡交叉
- **價值投資**：巴菲特風格（低 P/E、強護城河、長期持有）
- **動能策略**：強勢股追漲、弱勢股停損出場
- **均線策略**：多頭排列買進、空頭排列觀望
- **停損原則**：固定比例停損（如 5%）、移動停損、時間停損

## 台股規則
- 交易單位：一張 = 1000 股
- 漲跌幅限制：±10%
- 當沖需同日買賣同一檔

回答請使用繁體中文，技術術語可保留英文縮寫。
提供具體可行的建議，避免模糊回答。"""


def format_messages_for_ollama(messages: list[ChatMessage]) -> list[dict]:
    return [{"role": m.role, "content": m.content} for m in messages]


def extract_chat_reply(response: dict | None) -> str:
    try:
        return response["message"]["content"]
    except (KeyError, TypeError):
        return ""


def call_ollama_chat(
    messages: list[dict],
    model: str | None = None,
    timeout: int | None = None,
) -> dict:
    resp = requests.post(
        f"{config.OLLAMA_URL}/api/chat",
        json={
            "model": model or config.DECISION_MODEL,
            "messages": messages,
            "stream": False,
        },
        timeout=timeout or config.OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def call_anthropic_chat(messages: list[ChatMessage]) -> str:
    """Send conversation to Anthropic Sonnet and return reply text."""
    system_content = ""
    api_messages = []
    for m in messages:
        if m.role == "system":
            system_content = m.content
        else:
            api_messages.append({"role": m.role, "content": m.content})

    if not api_messages:
        return ""

    prompt = ""
    if system_content:
        prompt = f"{system_content}\n\n"
    for m in api_messages:
        label = "用戶" if m["role"] == "user" else "助理"
        prompt += f"[{label}]\n{m['content']}\n\n"
    prompt += "[助理]\n"

    return call_sonnet(prompt)


def get_quick_prompts() -> list[dict]:
    return [
        {
            "label": "🔍 分析持倉風險",
            "prompt": "請幫我分析目前持倉的風險和機會，有需要調整的地方嗎？",
        },
        {
            "label": "📈 RSI 策略",
            "prompt": "請解釋 RSI 指標怎麼用在台股操作上，什麼數值要買、什麼數值要賣？",
        },
        {
            "label": "🛡️ 停損策略",
            "prompt": "我應該怎麼設定停損點？有哪些常見的停損策略適合台股？",
        },
        {
            "label": "💰 巴菲特選股",
            "prompt": "用巴菲特價值投資的方式，我在台股應該怎麼選股？有哪些指標要看？",
        },
        {
            "label": "📊 均線操作",
            "prompt": "MA5、MA20、MA60 均線策略怎麼操作？黃金交叉和死亡交叉實際怎麼用？",
        },
        {
            "label": "⚡ 今日適合進場嗎",
            "prompt": "根據目前市場狀況和我的持倉，今天適合進場操作嗎？應該注意什麼？",
        },
    ]
