"""
playbook_updater.py — Playbook 自我改進模組（PlaybookUpdateJob）

設計契約見 docs/research_loop_design.md §2.5。

流程：
  load_today_outcomes()（讀 stock_prediction_log 含歸因欄位）
  → build_update_prompt()（每日=觀察追加；週五且樣本≥30=允許規則整併）
  → 呼叫 ai_client.call_haiku（輕量、快速）
  → apply_adaptive_section()（只動標記區、驗證長度與標記完整性）
  → git add + commit
  → Telegram 通知 diff
  → 任何步驟失敗：記 log、保留原檔、不得影響隔日流程

fail-safe 原則：
  - run_daily_update 永不 raise；失敗時回傳 False
  - git/notify 失敗只 log，不影響主流程
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import date
from datetime import date as _dt_date
from pathlib import Path
from typing import Optional

from ai_client import call_haiku
from logger import get_logger
import notifier

log = get_logger(__name__)

# ── Marker 常數（與 research_playbook.md 結構一致）────────────────────────────
FIXED_START    = "<!-- FIXED-SECTION-START：此區只有 Tim 能修改 -->"
FIXED_END      = "<!-- FIXED-SECTION-END -->"
ADAPTIVE_START = "<!-- ADAPTIVE-SECTION-START：此區由盤後 PlaybookUpdateJob 維護 -->"
ADAPTIVE_END   = "<!-- ADAPTIVE-SECTION-END -->"

# 自適應區長度上限（字元）
_ADAPTIVE_MAX_CHARS = 3000

# 觀察紀錄最大保留條數
_MAX_OBSERVATIONS = 20

# 觀察紀錄每條前綴（以 "- " 開頭，用於識別）
_OBS_PREFIX = "- "


# ═══════════════════════════════════════════════════════════════════════════
# 一、標記分割
# ═══════════════════════════════════════════════════════════════════════════

def split_sections(text: str) -> tuple[str, str]:
    """將 playbook 文字分割為固定區與自適應區。

    Args:
        text: 完整 playbook 文字（含四個 markers）。

    Returns:
        (fixed_content, adaptive_content) — 均不含 markers 本身。

    Raises:
        ValueError: 任何一個 marker 缺失時拋出，訊息含缺失 marker 名稱。
    """
    # 驗證四個 markers 都存在
    _required = [
        (FIXED_START,    "FIXED-SECTION-START"),
        (FIXED_END,      "FIXED-SECTION-END"),
        (ADAPTIVE_START, "ADAPTIVE-SECTION-START"),
        (ADAPTIVE_END,   "ADAPTIVE-SECTION-END"),
    ]
    for marker, label in _required:
        if marker not in text:
            raise ValueError(f"playbook 缺少 marker：{label}")

    # 擷取固定區（FIXED_START 之後、FIXED_END 之前）
    fs_idx = text.index(FIXED_START) + len(FIXED_START)
    fe_idx = text.index(FIXED_END)
    fixed = text[fs_idx:fe_idx].strip("\n")

    # 擷取自適應區（ADAPTIVE_START 之後、ADAPTIVE_END 之前）
    as_idx = text.index(ADAPTIVE_START) + len(ADAPTIVE_START)
    ae_idx = text.index(ADAPTIVE_END)
    adaptive = text[as_idx:ae_idx].strip("\n")

    return fixed, adaptive


# ═══════════════════════════════════════════════════════════════════════════
# 二、自適應區替換
# ═══════════════════════════════════════════════════════════════════════════

def apply_adaptive_section(full_text: str, new_adaptive: str) -> str:
    """僅替換 ADAPTIVE markers 之間的內容。

    驗證：
    - new_adaptive 長度 ≤ 3000 字元，否則拒絕並回傳 full_text。
    - full_text 本身必須含有完整四個 markers，否則回傳 full_text。
    - 替換後四個 markers 仍完整存在。

    Args:
        full_text:    原始完整 playbook 文字。
        new_adaptive: 新的自適應區內容（不含 markers）。

    Returns:
        更新後的完整 playbook 文字，或原始文字（失敗時）。
    """
    # 驗證輸入文字的 markers 完整性
    try:
        split_sections(full_text)
    except ValueError as e:
        log.warning("apply_adaptive_section：輸入文字 markers 不完整，保留原始：%s", e)
        return full_text

    # 驗證長度上限
    if len(new_adaptive) > _ADAPTIVE_MAX_CHARS:
        log.warning(
            "apply_adaptive_section：新自適應區長度 %d 超過上限 %d，拒絕更新",
            len(new_adaptive), _ADAPTIVE_MAX_CHARS,
        )
        return full_text

    # 執行替換
    as_idx = full_text.index(ADAPTIVE_START) + len(ADAPTIVE_START)
    ae_idx = full_text.index(ADAPTIVE_END)
    result = (
        full_text[:as_idx]
        + "\n" + new_adaptive + "\n"
        + full_text[ae_idx:]
    )

    # 後置驗證：markers 仍完整
    for marker in (FIXED_START, FIXED_END, ADAPTIVE_START, ADAPTIVE_END):
        if marker not in result:
            log.error("apply_adaptive_section：替換後 marker 遺失！保留原始。")
            return full_text

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 三、觀察紀錄上限
# ═══════════════════════════════════════════════════════════════════════════

def enforce_observation_cap(adaptive_text: str, max_obs: int = _MAX_OBSERVATIONS) -> str:
    """確保觀察紀錄不超過 max_obs 條；超過時淘汰最舊的（最前面的）。

    觀察紀錄以 "- " 開頭的行識別。非觀察行（標題、空行）保持不變。

    Args:
        adaptive_text: 自適應區文字。
        max_obs:       最多保留條數（預設 20）。

    Returns:
        處理後的自適應區文字。
    """
    lines = adaptive_text.splitlines(keepends=True)

    # 找出所有觀察行的索引
    obs_indices = [i for i, line in enumerate(lines) if line.startswith(_OBS_PREFIX)]

    if len(obs_indices) <= max_obs:
        return adaptive_text

    # 計算需要移除的條數
    to_remove = len(obs_indices) - max_obs
    remove_set = set(obs_indices[:to_remove])

    result_lines = [line for i, line in enumerate(lines) if i not in remove_set]
    return "".join(result_lines)


# ═══════════════════════════════════════════════════════════════════════════
# 四、讀取當日交易結果
# ═══════════════════════════════════════════════════════════════════════════

def load_today_outcomes(db_path: str, day: date) -> list[dict]:
    """從 stock_prediction_log 讀取指定日期的所有預測結果。

    防禦性設計：
    - DB 不存在或查詢失敗 → 回傳空清單，不 raise。
    - reason/factors_json/news_refs/youtube_refs 欄位不存在（舊版 schema）→ 填 None。

    Args:
        db_path: SQLite DB 路徑。
        day:     要查詢的日期。

    Returns:
        dict 清單，每筆含 stock_prediction_log 所有欄位。
        新欄位不存在時填 None。
    """
    if not Path(db_path).exists():
        log.info("load_today_outcomes：DB 不存在（%s），回傳空清單", db_path)
        return []

    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        day_str = day.isoformat()

        # 先取得表格欄位清單（PRAGMA 防禦）
        try:
            pragma_rows = conn.execute(
                "PRAGMA table_info(stock_prediction_log)"
            ).fetchall()
            existing_cols = {r["name"] for r in pragma_rows}
        except Exception:
            existing_cols = set()

        rows = conn.execute(
            "SELECT * FROM stock_prediction_log WHERE date = ? ORDER BY code",
            (day_str,),
        ).fetchall()
        conn.close()

        results = []
        _new_cols = ("reason", "factors_json", "news_refs", "youtube_refs")
        for row in rows:
            d: dict = dict(row)
            # 補上新欄位（若舊版 schema 中不存在）
            for col in _new_cols:
                if col not in existing_cols:
                    d[col] = None
                # 若欄位存在但未在 dict 中（理論上不會），也補 None
                d.setdefault(col, None)
            results.append(d)

        return results

    except Exception as e:
        log.error("load_today_outcomes 失敗（%s）：%s", db_path, e)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# 五、週五 gating 判斷
# ═══════════════════════════════════════════════════════════════════════════

def _should_do_weekly(day: date, n_samples: int) -> bool:
    """判斷是否應執行每週規則整併模式。

    條件：當天是週五（weekday() == 4）且累積樣本數 ≥ 30。

    Args:
        day:       判斷日期。
        n_samples: 可用樣本總數。

    Returns:
        True 若應執行週五整併模式，否則 False。
    """
    return day.weekday() == 4 and n_samples >= 30


# ═══════════════════════════════════════════════════════════════════════════
# 六、建立 AI 更新 prompt
# ═══════════════════════════════════════════════════════════════════════════

def build_no_data_observation(day) -> str:
    """今日無預測資料時的觀察紀錄——**不呼叫 LLM**。

    沒有素材卻要模型「根據今日交易結果」寫觀察，它就會編。2026-09-03 那次
    產出的「年底最後一週市場參與度進一步下降、成交量處於極低水位、外資籌碼
    活動維持靜止、融資水位控制表現維持穩定」——當天完全沒有任何資料，
    每一句都是憑空生成。

    這種情況下唯一能確定為真的事實就是「沒有資料」，所以直接寫死。
    """
    return f"- {day.isoformat()}：今日無預測資料產出，無可歸因之交易結果。"


def append_observation(adaptive_text: str, observation: str) -> str:
    """在「## 觀察紀錄」區段末尾追加一條觀察（不經 LLM）。

    找不到該區段時追加在文末——保守處理，寧可位置不完美也不要丟掉紀錄。
    """
    body = adaptive_text.rstrip("\n")
    return f"{body}\n{observation}\n"


def enforce_observation_date(text: str, day) -> str:
    """把**最後一條**觀察紀錄的日期改成系統日期。

    只動最後一條：其餘是歷史紀錄，日期是既成事實，不能改。
    模型即使被明確告知日期仍可能寫錯（或寫成未來），這是最後一道防線。
    """
    import re as _re

    lines = text.splitlines()
    pattern = _re.compile(r"^(\s*-\s*)(\d{4}-\d{2}-\d{2})(\s*[：:])")
    for i in range(len(lines) - 1, -1, -1):
        m = pattern.match(lines[i])
        if not m:
            continue
        if m.group(2) != day.isoformat():
            log.warning("Playbook 觀察日期不符（模型寫 %s，實際 %s），已更正",
                        m.group(2), day.isoformat())
            lines[i] = pattern.sub(
                lambda mm: f"{mm.group(1)}{day.isoformat()}{mm.group(3)}",
                lines[i], count=1,
            )
        break
    out = "\n".join(lines)
    return out + "\n" if text.endswith("\n") else out


def build_update_prompt(
    outcomes: list[dict],
    adaptive_text: str,
    is_weekly: bool = False,
    day: "date | None" = None,
) -> str:
    """組建傳給 AI 的 prompt。

    每日模式：要求追加 1 條帶日期的觀察（≤200字），觀察紀錄最多保留 20 條，
              超過時淘汰最舊的。
    每週模式：額外允許修改「當前研究重點」，但須附證據與樣本數。

    Args:
        outcomes:      當日交易結果清單（load_today_outcomes 的回傳值）。
        adaptive_text: 目前的自適應區全文（供 AI 上下文參考）。
        is_weekly:     True 時啟用每週規則整併模式。

    Returns:
        完整 prompt 字串。
    """
    # 組建結果摘要
    summary_lines = []
    for o in outcomes:
        code = o.get("code", "—")
        name = o.get("name", "—")
        correct = o.get("was_correct")
        ret = o.get("actual_return_pct")
        conf = o.get("confidence")
        reason = o.get("reason") or ""
        factors = o.get("factors_json") or ""

        correct_str = "正確" if correct else ("錯誤" if correct is False else "未知")
        ret_str = f"{ret:+.2f}%" if ret is not None else "—"
        conf_str = f"信心={conf}" if conf is not None else ""
        line = f"  - {code} {name}：{correct_str} {ret_str} {conf_str}"
        if reason:
            line += f"\n    理由：{reason[:100]}"
        if factors:
            line += f"\n    因子：{factors[:100]}"
        summary_lines.append(line)

    outcome_text = "\n".join(summary_lines) if summary_lines else "  （今日無預測資料）"

    # 每日模式指令
    #
    # 日期必須明確給出。原本寫「格式：- YYYY-MM-DD：觀察內容」卻沒告訴模型
    # 今天是幾號，2026-09-03 那次它就自己填了 2024-12-30，還順帶編出「年底
    # 最後一週市場參與度下降」這種完全沒發生的事。
    _day_str = (day or _dt_date.today()).isoformat()
    daily_instruction = (
        "【任務：每日觀察追加】\n"
        f"今日日期為 {_day_str}。\n"
        "請根據上方今日交易結果，在「## 觀察紀錄」區段末尾追加 1 條新觀察。\n"
        f"格式：`- {_day_str}：觀察內容`（日期必須完全照用，不得自行推算或更改）\n"
        "限制：\n"
        "  1. 每條觀察不超過 200 字。\n"
        "  2. 觀察紀錄最多保留 20 條；若現有觀察已達 20 條，請刪除最舊的 1 條再追加新的。\n"
        "  3. 不得修改「## 當前研究重點」區段內容。\n"
        "  4. 只允許追加觀察，不允許調整或重寫任何既有規則。\n"
        "  5. 只陳述上方資料實際顯示的事實。沒有資料支持的市場描述（成交量、"
        "外資動向、融資水位等）一律不得撰寫。\n"
    )

    # 每週模式額外指令
    weekly_extra = (
        "\n【額外任務：每週規則整併（僅限週五且樣本≥30）】\n"
        "你可以根據本週觀察與歷史資料，修訂「## 當前研究重點」。\n"
        "要求：\n"
        "  1. 每項規則變更須附上支持的樣本數（例如：「根據 35 筆樣本...」）。\n"
        "  2. 新增或修改的規則須有明確的市場觀察依據。\n"
        "  3. 不可刪除既有規則，除非有相反的充分樣本證據。\n"
    ) if is_weekly else ""

    prompt = (
        f"你是台股 AI 量化交易系統的研究助理。\n"
        f"以下是今日（盤後）的個股預測結果與歸因資訊：\n\n"
        f"{outcome_text}\n\n"
        f"以下是目前的研究作業手冊自適應區（供上下文參考）：\n"
        f"---\n{adaptive_text}\n---\n\n"
        f"{daily_instruction}"
        f"{weekly_extra}\n"
        f"【輸出規則】\n"
        f"- 只輸出更新後的完整自適應區文字（從「## 當前研究重點」到最後一條觀察）。\n"
        f"- 不要輸出任何解釋、前言、或 Markdown 代碼塊標記（```）。\n"
        f"- 保持繁體中文。\n"
    )
    return prompt


# ═══════════════════════════════════════════════════════════════════════════
# 七、git commit
# ═══════════════════════════════════════════════════════════════════════════

def git_commit_playbook(playbook_path: str, day: date) -> None:
    """git add + commit research_playbook.md。

    失敗時只 log，不 raise（fail-safe）。

    Args:
        playbook_path: playbook 檔案的絕對路徑。
        day:           更新日期（用於 commit 訊息）。
    """
    commit_msg = f"playbook: {day.isoformat()} 盤後更新"
    try:
        subprocess.run(
            ["git", "add", playbook_path],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            check=True, capture_output=True, text=True,
        )
        log.info("git_commit_playbook：commit 成功（%s）", commit_msg)
    except Exception as e:
        log.warning("git_commit_playbook 失敗（非致命）：%s", e)


# ═══════════════════════════════════════════════════════════════════════════
# 八、Telegram 通知
# ═══════════════════════════════════════════════════════════════════════════

def notify_playbook_update(
    old_adaptive: str,
    new_adaptive: str,
    day: date,
) -> None:
    """透過 notifier 發送 playbook 更新通知。

    失敗時只 log，不 raise（fail-safe）。

    Args:
        old_adaptive: 更新前的自適應區文字。
        new_adaptive: 更新後的自適應區文字。
        day:          更新日期。
    """
    try:
        # 找出新增的觀察行（簡易 diff）
        old_lines = set(old_adaptive.splitlines())
        new_lines = new_adaptive.splitlines()
        added = [l for l in new_lines if l.startswith(_OBS_PREFIX) and l not in old_lines]

        diff_summary = "\n".join(added[:5]) if added else "（無新增觀察）"

        msg = (
            f"<b>研究手冊（Playbook）盤後更新</b>\n"
            f"日期：{day.isoformat()}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"新增觀察：\n{diff_summary}"
        )
        notifier._send(msg)
        log.info("notify_playbook_update：通知已發送")
    except Exception as e:
        log.warning("notify_playbook_update 失敗（非致命）：%s", e)


# ═══════════════════════════════════════════════════════════════════════════
# 九、主 orchestrator
# ═══════════════════════════════════════════════════════════════════════════

def run_daily_update(
    playbook_path: Optional[str] = None,
    db_path: Optional[str] = None,
    day: Optional[date] = None,
) -> bool:
    """PlaybookUpdateJob 主流程（fail-safe orchestrator）。

    任何步驟失敗：log 錯誤、保留原始 playbook 檔案、回傳 False。
    永不 raise（供排程器安全呼叫）。

    Args:
        playbook_path: research_playbook.md 的路徑（預設讀 env PLAYBOOK_PATH，
                       再退回 "research_playbook.md"）。
        db_path:       learning.db 的路徑（預設讀 env DB_PATH，再退回
                       "data/learning.db"）——讓 main.py 的 PlaybookUpdateJob
                       可無參數呼叫 run_daily_update()。
        day:           更新日期（預設為今天）。

    Returns:
        True 表示成功更新並 commit；False 表示任何失敗。
    """
    if playbook_path is None:
        playbook_path = os.getenv("PLAYBOOK_PATH", "research_playbook.md")
    if db_path is None:
        db_path = os.getenv("DB_PATH", "data/learning.db")
    if day is None:
        day = date.today()

    log.info("PlaybookUpdateJob 開始（%s）", day.isoformat())

    # ── 步驟 1：讀取 playbook ────────────────────────────────────────────
    try:
        pb_file = Path(playbook_path)
        if not pb_file.exists():
            log.error("run_daily_update：playbook 不存在（%s）", playbook_path)
            return False
        original_text = pb_file.read_text(encoding="utf-8")
    except Exception as e:
        log.error("run_daily_update：讀取 playbook 失敗：%s", e)
        return False

    # ── 步驟 2：分割取得自適應區 ─────────────────────────────────────────
    try:
        _, adaptive_text = split_sections(original_text)
    except ValueError as e:
        log.error("run_daily_update：playbook markers 不完整：%s", e)
        return False

    # ── 步驟 3：讀取當日結果 ─────────────────────────────────────────────
    try:
        outcomes = load_today_outcomes(db_path, day)
    except Exception as e:
        log.error("run_daily_update：load_today_outcomes 例外：%s", e)
        outcomes = []

    # ── 步驟 4：判斷是否為週五模式 ───────────────────────────────────────
    is_weekly = _should_do_weekly(day, n_samples=len(outcomes))

    # ── 步驟 5：建立 prompt ──────────────────────────────────────────────
    try:
        prompt = build_update_prompt(outcomes, adaptive_text,
                                     is_weekly=is_weekly, day=day)
    except Exception as e:
        log.error("run_daily_update：build_update_prompt 失敗：%s", e)
        return False

    # ── 步驟 6：產生新的自適應區 ─────────────────────────────────────────
    #
    # 無預測資料時**不呼叫 LLM**：沒有素材卻要它「根據今日交易結果」寫觀察，
    # 它就會編。2026-09-03 那次產出「年底最後一週市場參與度進一步下降、
    # 成交量處於極低水位、外資籌碼活動維持靜止」——當天完全沒有資料，
    # 每一句都是憑空生成，日期還寫成 2024-12-30。
    if not outcomes:
        log.info("run_daily_update：今日無預測資料，直接追加事實紀錄（不呼叫 AI）")
        new_adaptive = append_observation(adaptive_text,
                                          build_no_data_observation(day))
    else:
        try:
            new_adaptive = call_haiku(prompt)
        except Exception as e:
            log.error("run_daily_update：AI 呼叫失敗：%s", e)
            return False

        if not new_adaptive or not new_adaptive.strip():
            log.error("run_daily_update：AI 回傳空字串，放棄更新")
            return False

    # 最後一道防線：即使 prompt 已明確給出日期，模型仍可能寫錯或寫成未來。
    try:
        new_adaptive = enforce_observation_date(new_adaptive, day)
    except Exception as e:
        log.warning("run_daily_update：日期校正失敗，沿用原輸出：%s", e)

    # ── 步驟 7：淘汰超額觀察 ─────────────────────────────────────────────
    try:
        new_adaptive = enforce_observation_cap(new_adaptive)
    except Exception as e:
        log.warning("run_daily_update：enforce_observation_cap 失敗，使用原始 AI 輸出：%s", e)

    # ── 步驟 8：替換自適應區 ─────────────────────────────────────────────
    try:
        new_text = apply_adaptive_section(original_text, new_adaptive)
    except Exception as e:
        log.error("run_daily_update：apply_adaptive_section 失敗：%s", e)
        return False

    if new_text == original_text:
        # apply_adaptive_section 在失敗時保留原始文字；此情境視為失敗
        log.error("run_daily_update：apply_adaptive_section 未實際更新（可能因驗證失敗）")
        return False

    # ── 步驟 9：寫入檔案 ─────────────────────────────────────────────────
    try:
        pb_file.write_text(new_text, encoding="utf-8")
        log.info("run_daily_update：playbook 已更新（%s）", playbook_path)
    except Exception as e:
        log.error("run_daily_update：寫入 playbook 失敗：%s", e)
        return False

    # ── 步驟 10：git commit（失敗容忍）──────────────────────────────────
    git_commit_playbook(playbook_path, day)

    # ── 步驟 11：Telegram 通知（失敗容忍）───────────────────────────────
    notify_playbook_update(adaptive_text, new_adaptive, day)

    log.info("PlaybookUpdateJob 完成（%s）", day.isoformat())
    return True
