"""
youtube_analyzer.py — 每日 YouTube 名嘴分析模組

直接將 YouTube URL 傳給 Gemini，讓 AI 自行觀看影片並分析當日觀點，
結果存入 data/youtube_analysis.json 並推送 Telegram。

用法：
    python3 youtube_analyzer.py              # 分析今天最新影片
    python3 youtube_analyzer.py --send       # 分析 + 推 Telegram
    python3 youtube_analyzer.py --days 3     # 最近 3 天的影片
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# ── 監控頻道清單（可新增）────────────────────────────────────────────────────
# url 可以是 channel/videos、playlist?list=...、或 @handle
CHANNELS = [
    {"name": "股市名嘴",        "url": "https://www.youtube.com/channel/UCuzqko_GKcj9922M1gUo__w/videos"},
    {"name": "EBC 東森財經",    "url": "https://www.youtube.com/@EBCmoneyshow"},
    {"name": "財經播放清單 A1", "url": "https://www.youtube.com/playlist?list=PLVu0pIxQ7F-yvxR_dCP_zBgChK3s84b99"},
    {"name": "財經播放清單 A2", "url": "https://www.youtube.com/playlist?list=PLVu0pIxQ7F-y5amt3ePI8nlFGLvMZtBPF"},
    {"name": "財經播放清單 B",  "url": "https://www.youtube.com/playlist?list=PL9mUJWHev0Kl3SNj3xMRV0SkD9K2TY2HA"},
]

_OUTPUT_PATH = "data/youtube_analysis.json"


# ── yt-dlp 取最新影片清單 ─────────────────────────────────────────────────────

def _get_recent_videos(channel_url: str, days: int = 1) -> list[dict]:
    """透過 yt-dlp 取得最近 N 天的影片清單（支援 channel、playlist、@handle）。"""
    try:
        import subprocess
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--no-check-certificates",
            "--print", "%(id)s\t%(title)s\t%(description)s\t%(upload_date)s",
            "--playlist-end", "5",
            channel_url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        videos = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 3)
            if len(parts) < 2:
                continue
            vid_id      = parts[0].strip()
            title       = parts[1].strip()
            description = parts[2].strip() if len(parts) > 2 else ""
            upload_date = parts[3].strip() if len(parts) > 3 else ""
            if upload_date and upload_date < cutoff:
                continue
            videos.append({
                "video_id":    vid_id,
                "title":       title,
                "description": description[:800],
                "url":         f"https://www.youtube.com/watch?v={vid_id}",
                "published":   upload_date,
            })
        return videos
    except Exception as e:
        log.warning("yt-dlp 取影片失敗：%s", e)
        return []


# ── Gemini 直接分析 YouTube 影片 ──────────────────────────────────────────────

_ANALYSIS_PROMPT = """你是一位台股分析師助理。請觀看這支影片，分析其中的台股投資觀點。

請以 JSON 格式回傳：
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "sentiment_zh": "看多" | "看空" | "中性",
  "key_stocks": [
    {{"code": "股票代號或空字串", "name": "股票名稱", "view": "看多/看空/注意", "reason": "簡短原因"}}
  ],
  "key_sectors": ["半導體", "AI", ...],
  "main_points": ["重點1", "重點2", "重點3"],
  "risk_warnings": ["風險提示1", ...],
  "one_line": "一句話總結今日觀點"
}}

影片標題：{title}

只回傳 JSON，不要其他說明。
如果影片與台股無關，sentiment 設為 neutral，key_stocks/key_sectors 填空陣列，one_line 填「非台股相關內容」。"""


_gemini_singleton = None

def _gemini_client():
    global _gemini_singleton
    if _gemini_singleton is None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY 未設定")
        _gemini_singleton = genai.Client(api_key=api_key)
    return _gemini_singleton


def _parse_gemini_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    return {"error": "parse_failed", "raw": raw[:200], "one_line": "AI 分析格式錯誤"}


def _analyze_video(video_url: str, title: str, description: str = "") -> dict:
    """直接把 YouTube URL 丟給 Gemini；影片太長時 fallback 改用描述分析。"""
    from google.genai import types

    prompt = _ANALYSIS_PROMPT.format(title=title)

    # ── 嘗試直接看影片 ────────────────────────────────────────────────────────
    try:
        resp = _gemini_client().models.generate_content(
            model="gemini-2.5-flash",
            contents=types.Content(
                role="user",
                parts=[
                    types.Part(file_data=types.FileData(
                        file_uri=video_url, mime_type="video/youtube"
                    )),
                    types.Part(text=prompt),
                ],
            ),
        )
        return _parse_gemini_json(resp.text or "")

    except Exception as e:
        err = str(e)
        # 影片超過 token 上限 → 改用描述文字
        if "token" in err.lower() or "INVALID_ARGUMENT" in err:
            log.info("    影片超長，改用描述分析：%s", video_url)
            return _analyze_text(title, description)
        log.warning("Gemini 影片分析失敗 %s：%s", video_url, e)
        return {"error": err, "one_line": "AI 分析失敗"}


def _analyze_text(title: str, description: str) -> dict:
    """無法直接看影片時，用標題 + 描述做純文字分析。"""
    if not description:
        return {"error": "no_description", "one_line": "影片過長且無描述，無法分析"}

    text_prompt = (
        _ANALYSIS_PROMPT.format(title=title)
        + f"\n\n影片描述（替代字幕）：\n{description[:800]}"
    )
    try:
        resp = _gemini_client().models.generate_content(
            model="gemini-2.5-flash",
            contents=text_prompt,
        )
        return _parse_gemini_json(resp.text or "")
    except Exception as e:
        log.warning("純文字分析也失敗：%s", e)
        return {"error": str(e), "one_line": "AI 分析失敗"}


# ── Telegram 推送 ─────────────────────────────────────────────────────────────

def _send_telegram(results: list[dict]) -> None:
    try:
        import requests
        from dotenv import load_dotenv
        load_dotenv(override=True)

        token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            log.warning("Telegram 未設定，略過推送")
            return

        lines = ["📺 <b>今日 YouTube 名嘴分析</b>", ""]

        for r in results:
            ch   = r["channel_name"]
            vids = r.get("videos", [])
            if not vids:
                lines.append(f"📭 {ch}：今日無新影片")
                continue

            for v in vids:
                a = v.get("analysis", {})
                sentiment_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(
                    a.get("sentiment", "neutral"), "⚪"
                )
                lines.append(f"{sentiment_icon} <b>{ch}</b>")
                lines.append(f"📌 {v['title'][:40]}")
                lines.append(f"💬 {a.get('one_line', '—')}")

                stocks = a.get("key_stocks", [])[:3]
                if stocks:
                    stock_str = "  ".join(
                        f"{'🟢' if s['view']=='看多' else '🔴' if s['view']=='看空' else '🟡'}"
                        f"{s.get('code','')} {s['name']}"
                        for s in stocks
                    )
                    lines.append(f"🏷 {stock_str}")

                sectors = a.get("key_sectors", [])[:3]
                if sectors:
                    lines.append(f"📊 題材：{' · '.join(sectors)}")

                lines.append("")

        text = "\n".join(lines)
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        log.info("Telegram 推送完成")
    except Exception as e:
        log.warning("Telegram 推送失敗：%s", e)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(days: int = 1, send_telegram: bool = False) -> list[dict]:
    """分析所有頻道最近 N 天影片，回傳結果清單，並存檔。"""
    from dotenv import load_dotenv
    load_dotenv(override=True)

    Path("data").mkdir(exist_ok=True)
    all_results = []

    for channel in CHANNELS:
        log.info("處理頻道：%s", channel["name"])
        videos = _get_recent_videos(channel["url"], days=days)

        if not videos:
            log.info("  無新影片（最近 %d 天）", days)
            all_results.append({"channel_name": channel["name"], "videos": []})
            continue

        log.info("  找到 %d 支影片，交給 Gemini 逐一分析...", len(videos))
        analyzed_videos = []

        for v in videos:
            log.info("  分析：%s", v["title"][:60])
            analysis = _analyze_video(v["url"], v["title"], v.get("description", ""))
            v["analysis"] = analysis
            analyzed_videos.append(v)

            log.info("    → %s  |  %s",
                     analysis.get("sentiment_zh", "—"),
                     analysis.get("one_line", "—")[:60])

        all_results.append({
            "channel_name": channel["name"],
            "channel_url":  channel["url"],
            "date":         date.today().isoformat(),
            "videos":       analyzed_videos,
        })

    output = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "channels": all_results,
    }
    with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info("結果已存至 %s", _OUTPUT_PATH)

    if send_telegram:
        _send_telegram(all_results)

    return all_results


def get_latest_summary() -> str:
    """給 morning_briefing 呼叫，回傳今日摘要文字。"""
    try:
        with open(_OUTPUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        lines = ["【YouTube 名嘴觀點】"]
        for ch in data.get("channels", []):
            for v in ch.get("videos", []):
                a = v.get("analysis", {})
                if a.get("one_line") in ("非台股相關內容", None, ""):
                    continue
                lines.append(f"・{ch['channel_name']}：{a.get('one_line', '—')}")
                stocks = a.get("key_stocks", [])[:2]
                for s in stocks:
                    lines.append(f"  → {s['view']} {s.get('code','')} {s['name']}：{s['reason'][:30]}")
        return "\n".join(lines) if len(lines) > 1 else "今日無 YouTube 分析資料"
    except Exception:
        return "今日無 YouTube 分析資料"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube 名嘴每日分析")
    parser.add_argument("--days", type=int, default=1, help="分析最近幾天的影片（預設 1）")
    parser.add_argument("--send", action="store_true",  help="推送到 Telegram")
    args = parser.parse_args()

    results = run(days=args.days, send_telegram=args.send)
    print()
    print(get_latest_summary())
