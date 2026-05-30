"""
youtube_analyzer.py — 每日 YouTube 名嘴分析模組

自動抓取指定頻道最新影片字幕，用 Gemini AI 分析當日觀點，
結果存入 data/youtube_analysis.json 並推送 Telegram。

用法：
    python3 youtube_analyzer.py              # 分析今天最新影片
    python3 youtube_analyzer.py --send       # 分析 + 推 Telegram
    python3 youtube_analyzer.py --days 3     # 最近 3 天的影片

套件需求：
    pip install youtube-transcript-api feedparser
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
CHANNELS = [
    {
        "name": "股市名嘴",
        "channel_id": "UCuzqko_GKcj9922M1gUo__w",
    },
    # 新增頻道只要加一行：
    # {"name": "XXX財經", "channel_id": "UC..."},
]

_OUTPUT_PATH = "data/youtube_analysis.json"
_TRANSCRIPT_LANGS = ["zh-TW", "zh-Hant", "zh", "en"]


# ── RSS 取最新影片 ─────────────────────────────────────────────────────────────

def _get_recent_videos(channel_id: str, days: int = 1) -> list[dict]:
    """透過 yt-dlp 取得最近 N 天的影片清單（含描述）。"""
    try:
        import subprocess, json as _json
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--no-check-certificates",
            "--print", "%(id)s\t%(title)s\t%(description)s\t%(upload_date)s",
            "--playlist-end", "10",
            f"https://www.youtube.com/channel/{channel_id}/videos",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        videos = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 3)
            if len(parts) < 2:
                continue
            vid_id     = parts[0].strip()
            title      = parts[1].strip()
            description = parts[2].strip() if len(parts) > 2 else ""
            upload_date = parts[3].strip() if len(parts) > 3 else ""
            if upload_date and upload_date < cutoff:
                continue
            videos.append({
                "video_id":    vid_id,
                "title":       title,
                "description": description[:1000],
                "url":         f"https://www.youtube.com/watch?v={vid_id}",
                "published":   upload_date,
            })
        return videos
    except Exception as e:
        log.warning("yt-dlp 取影片失敗：%s", e)
        return []


# ── 取字幕（yt-dlp 自動字幕）─────────────────────────────────────────────────

def _get_transcript(video_id: str) -> str:
    """嘗試用 yt-dlp 抓自動字幕，失敗則回傳空字串。"""
    import subprocess, tempfile, os, glob

    # 先試 youtube-transcript-api
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        segments = YouTubeTranscriptApi.get_transcript(video_id, languages=_TRANSCRIPT_LANGS)
        text = " ".join(s["text"] for s in segments)
        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        pass

    # 改用 yt-dlp 下載自動字幕
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                "yt-dlp",
                "--no-check-certificates",
                "--write-auto-sub",
                "--sub-lang", "zh-TW,zh-Hant,zh,en",
                "--sub-format", "vtt",
                "--skip-download",
                "--output", os.path.join(tmpdir, "%(id)s.%(ext)s"),
                f"https://www.youtube.com/watch?v={video_id}",
            ]
            subprocess.run(cmd, capture_output=True, timeout=60)

            # 找 .vtt 檔案
            vtt_files = glob.glob(os.path.join(tmpdir, "*.vtt"))
            if not vtt_files:
                return ""

            raw = open(vtt_files[0], encoding="utf-8").read()
            # 清除 VTT 格式標記，取純文字
            text = re.sub(r"WEBVTT.*?\n\n", "", raw, flags=re.DOTALL)
            text = re.sub(r"\d{2}:\d{2}:\d{2}\.\d+ --> .*\n", "", text)
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\n+", " ", text).strip()
            return text[:4000]
    except Exception as e:
        log.info("yt-dlp 字幕失敗 %s：%s", video_id, e)
        return ""


# ── Gemini 分析 ───────────────────────────────────────────────────────────────

_ANALYSIS_PROMPT = """
你是一位台股分析師助理。以下是今天一位台股財經 YouTuber 的影片內容。

請分析並以 JSON 格式回傳：
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
{content_label}：
{content}

只回傳 JSON，不要其他說明。
"""


def _analyze_with_ai(title: str, transcript: str, description: str = "") -> dict:
    """用 Gemini 分析字幕或描述，回傳結構化結果。"""
    if transcript:
        content = transcript[:3000]
        content_label = "字幕內容（前 3000 字）"
    elif description:
        content = description[:1000]
        content_label = "影片描述"
        log.info("    （無字幕，改用描述分析）")
    else:
        return {"error": "no_content", "one_line": "無字幕與描述，無法分析"}

    try:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from ai_client import call_gemini_with_search, call_gemini

        prompt = _ANALYSIS_PROMPT.format(
            title=title,
            content_label=content_label,
            content=content,
        )
        # 先試 Gemini Search（有新聞 grounding）
        try:
            raw = call_gemini_with_search(prompt)
        except Exception:
            raw = call_gemini(prompt)

        # 取出 JSON
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        return {"error": "parse_failed", "raw": raw[:200], "one_line": "AI 分析格式錯誤"}
    except Exception as e:
        log.warning("AI 分析失敗：%s", e)
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
    """
    分析所有頻道最近 N 天影片，回傳結果清單，並存檔。
    """
    from dotenv import load_dotenv
    load_dotenv(override=True)

    Path("data").mkdir(exist_ok=True)
    all_results = []

    for channel in CHANNELS:
        log.info("處理頻道：%s (%s)", channel["name"], channel["channel_id"])
        videos = _get_recent_videos(channel["channel_id"], days=days)

        if not videos:
            log.info("  無新影片（最近 %d 天）", days)
            all_results.append({"channel_name": channel["name"], "videos": []})
            continue

        log.info("  找到 %d 支影片", len(videos))
        analyzed_videos = []

        for v in videos:
            log.info("  分析：%s", v["title"])
            transcript = _get_transcript(v["video_id"])
            analysis   = _analyze_with_ai(v["title"], transcript, v.get("description", ""))

            v["transcript_len"] = len(transcript)
            v["analysis"]       = analysis
            analyzed_videos.append(v)

            # 簡易 log
            log.info("    → %s  |  %s",
                     analysis.get("sentiment_zh", "—"),
                     analysis.get("one_line", "—")[:50])

        all_results.append({
            "channel_name": channel["name"],
            "channel_id":   channel["channel_id"],
            "date":         date.today().isoformat(),
            "videos":       analyzed_videos,
        })

    # 存檔
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
                lines.append(f"・{ch['channel_name']}：{a.get('one_line', '—')}")
                stocks = a.get("key_stocks", [])[:2]
                for s in stocks:
                    lines.append(f"  → {s['view']} {s.get('code','')} {s['name']}：{s['reason'][:30]}")
        return "\n".join(lines) if len(lines) > 1 else "今日無 YouTube 分析資料"
    except Exception:
        return "今日無 YouTube 分析資料"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YouTube 名嘴每日分析")
    parser.add_argument("--days",   type=int, default=1,           help="分析最近幾天的影片（預設 1）")
    parser.add_argument("--send",   action="store_true",            help="推送到 Telegram")
    args = parser.parse_args()

    results = run(days=args.days, send_telegram=args.send)

    # 印出摘要
    print()
    print(get_latest_summary())
