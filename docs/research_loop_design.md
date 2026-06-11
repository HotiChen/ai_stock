# 研究迴圈設計：來源可追溯 + Playbook 自我改進

> 狀態：Tim 已批准（2026-06-10）。本文件是實作契約，所有 agent 依此開工。
> 變更本文件需 Tim 同意。

## 一、目標與迴圈

```
            ┌──────────────────────────────────────────────┐
            │  research_playbook.md（Gemini 的作業手冊）      │
            │  ├── 固定區：只有 Tim 能改                      │
            │  └── 自適應區：Claude 盤後更新                  │
            └──────────────┬───────────────────────────────┘
                           │ 早上：Gemini 讀手冊做研究
                           ▼
  youtube_analyzer / news_agent ──→ deep_analyzer（Claude 預測，附來源編號）
                           │
                           ▼ 入庫（含 factors_json / news_refs / youtube_refs）
                stock_prediction_log
                           │
                           ▼ 13:50 盤後（PostMarketJob 13:35 之後）
            PlaybookUpdateJob：Claude 讀當日結果＋來源歸因
                           │
            每日：追加觀察（append-only，帶日期）
            每週五：規則整併（樣本 ≥ 30 筆才允許改規則）
                           │
                           ▼
            更新自適應區 → git commit → Telegram 通知 diff（Tim 可否決）
```

## 二、資料契約（所有 agent 必須遵守的 schema）

### 2.1 `stock_prediction_log` 新增欄位（learning_db.py，ALTER TABLE，全部 nullable）

| 欄位 | 型別 | 內容 |
|---|---|---|
| `reason` | TEXT | pick 的完整理由全文（現有 `p.reason`，目前未入庫） |
| `factors_json` | TEXT | JSON：`{"technical": str, "chip": str, "news": str, "theme": str}` |
| `news_refs` | TEXT | JSON array：`[{"id": "N1", "title": str, "url": str, "published": str, "source": str}]`（僅 AI 實際引用的） |
| `youtube_refs` | TEXT | JSON array：`[{"video_id": str, "channel": str, "url": str, "published": str}]` |

### 2.2 `news_agent.py` headline 形狀（取代裸字串）

```python
{"title": str, "url": str, "published": str(ISO 8601), "source": str}  # source = 鉅亨網/Yahoo/MoneyDJ
```
`get_news_context()` 回傳 dict 的 `headlines` 改為上述 dict list；
為向後相容，保留 `headlines_text: list[str]`（純標題）。

### 2.3 `deep_analyzer.py` 新聞引用協定

- `build_deep_prompt` 注入新聞時編號 `[N1]…[N5]`，每則附 `published` 時間
- prompt 規則（件 2 邊界化）：
  1. 新聞/影片情緒僅作 catalyst 確認與風險檢核，單獨不得使 confidence 提升超過 +1
  2. 與技術/籌碼訊號矛盾時，以技術/籌碼為準
  3. 發布超過 24 小時的訊息視為已 price-in，僅作背景
- 要求 AI 回傳 `news_refs_used: ["N1", "N3"]`；`DeepAnalysis` 加 `news_refs` 欄位（解析回完整 dict）

### 2.4 `research_playbook.md` 結構（repo 根目錄）

```markdown
# 研究作業手冊（Gemini Research Playbook）

<!-- FIXED-SECTION-START：此區只有 Tim 能修改 -->
## 固定規範
（風險紀律、輸出格式、資料來源清單、絕對禁止事項）
<!-- FIXED-SECTION-END -->

<!-- ADAPTIVE-SECTION-START：此區由盤後 PlaybookUpdateJob 維護 -->
## 當前研究重點
（Claude 可改，規則變更需附證據與樣本數）

## 觀察紀錄（append-only，最多保留 20 條，舊的先淘汰）
- YYYY-MM-DD：…
<!-- ADAPTIVE-SECTION-END -->
```

- 自適應區長度上限 **3000 字元**；超過時必須先淘汰最舊觀察
- 程式更新時**只能**改寫 ADAPTIVE 標記之間的內容，違反 = bug

### 2.5 `playbook_updater.py`（新模組，repo 根目錄）

流程：`load_today_outcomes()`（讀 stock_prediction_log 含歸因欄位）
→ `build_update_prompt()`（每日=觀察追加；週五且樣本≥30=允許規則整併）
→ 呼叫既有 `ai_client` 的 Claude 介面
→ `apply_adaptive_section()`（只動標記區、驗證長度與標記完整性，失敗則保留原檔）
→ `git add research_playbook.md && git commit -m "playbook: YYYY-MM-DD 盤後更新"`
→ Telegram 通知 diff（用 notifier 既有函式，失敗不 raise）

任何一步失敗：記 log、保留原 playbook、不得影響隔日流程（fail safe）。

### 2.6 排程（main.py）

- 新增 `PlaybookUpdateJob`，**13:50** 執行（PostMarketJob 13:35 之後），僅交易日
- 比照既有 job 的防重複執行模式

### 2.7 Gemini 管線讀取手冊（youtube_analyzer.py）

- 分析影片的 prompt 前注入 playbook **全文**（固定區+自適應區）
- playbook 不存在或讀取失敗 → 行為與現狀完全相同（fail safe）

## 三、分工與模型

| Agent | 模型 | 範圍（檔案互斥） |
|---|---|---|
| F | Opus | 件1+件2：learning_db.py、news_agent.py、deep_analyzer.py、telegram_bot.py 入庫點 + tests |
| G | Sonnet | playbook 迴圈：research_playbook.md、playbook_updater.py + tests |
| H | Sonnet | 整合：youtube_analyzer.py 讀手冊、main.py 掛 PlaybookUpdateJob + tests |
| I | Haiku | .env.example 補項、TODO.md 記錄 |

全部 TDD：先寫失敗測試再實作，不得修改測試斷言遷就實作。
回歸標準：tests/ 既有 97 個失敗為環境基準線，不得新增失敗。
