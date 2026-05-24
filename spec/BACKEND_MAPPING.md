# Backend Mapping · Python 模組 → 前端 API

> 你的 `ai_stock/` 既有 Python 模組如何對應到前端畫面 + FastAPI router。
> 此文件假設要從 Streamlit (`app.py`) 遷移到 FastAPI + React。
> 若選擇保留 Streamlit，仍可參考 `app.py` section 的對應。

---

## 1. 既有模組總覽

| 模組 | 主要功能 | 對應前端畫面 |
|---|---|---|
| `main.py` | 排程器 (每 30s poll) | 背景，不直接對應 UI |
| `app.py` | Streamlit Dashboard | **整個前端要取代它** |
| `morning_briefing.py` | 盤前選股 Job | 03.1 Top N |
| `candidate_builder.py` | 候選股清單建構 | 03.1 (run 內部) |
| `deep_analyzer.py` | 個股深度分析 | 03.1 + 03.2 |
| `ai_client.py` | Anthropic API wrapper | 03.x 全部 + 05.2 chat |
| `chat_agent.py` | AI 顧問對話 | 05.2 |
| `executor.py` | 下單執行 | 04.3 |
| `monitor_agent.py` | 盤中監控 thread | 04.1 |
| `intraday_monitor.py` | 分時行情監控 | 04.1 + 04.2 |
| `alerts.py` | 警報資料模型 | 04.1 警報串流 |
| `risk_guard.py` | 風控 5 項檢查 | 03.1 + 04.3 + 06 |
| `user_confirm.py` | Telegram 確認 | 04.3 + 07.3 |
| `telegram_bot.py` | Telegram bot | 07.3 |
| `notifier.py` | 通知派送 | 06 通知偏好 |
| `portfolio.py` | 持倉計算 | 05.1 |
| `shioaji_portfolio.py` | Shioaji 持倉 | 05.1 |
| `daily_tracker.py` | 每日績效 | 02 KPI + 05.4 |
| `daytrading_analyzer.py` | 當沖分析 | 04.2 |
| `daytrading_db.py` | 當沖資料庫 | 04.x |
| `daytrading_monitor.py` | 當沖監控 | 04.1 |
| `daytrading_report.py` | 當沖報告 | 05.4 |
| `daytrading_review.py` | 當沖回顧 | 05.4 |
| `daytrading_config.py` | 當沖設定 | 06 |
| `technical_indicators.py` | 技術指標計算 | 03.2 + 04.2 |
| `rules.py` | 訊號評分 | 03.2 + 05.2 |
| `chip_data.py` | 籌碼資料 | 04.2 |
| `simulate.py` | 回測進入點 | 05.3 |
| `sim_engine.py` | 回測引擎 | 05.3 |
| `sim_plan_store.py` | 回測計畫存 | 05.3 |
| `sim_position_store.py` | 回測倉位存 | 05.3 |
| `sim_settlement.py` | 回測結算 | 05.3 |
| `learning_db.py` | 學習日誌 DB | 05.2 |
| `learning_report.py` | 學習報告 | 05.2 |
| `market_scan.py` `market_scanner.py` | 大盤掃描 | 05.5 |
| `market_context.py` | 市場背景 | 03.1 prompt + 05.5 |
| `market_index.py` | 指數資料 | 05.5 + StatusBar |
| `futures_premium.py` | 期貨價差 | 05.5 |
| `news_agent.py` | 新聞抓取 | 05.5 |
| `weekly_report.py` `weekly_report_runner.py` | 週報 | 05.4 |
| `morning_strategy.py` | 盤前策略 | 03.1 |
| `strategies.py` | 策略集 | 03.1 + 05.3 |
| `strategy_executor.py` | 策略執行 | 04.1 + 03.1 |
| `strategy_planner.py` | 策略規劃 | 03.1 |
| `strategy_tracker.py` | 策略追蹤 | 04.1 |
| `stock_detail.py` | 個股明細 | 03.2 |
| `stock_query.py` | 個股查詢 | 全域搜尋 |
| `stock_research.py` | 個股研究 | 03.2 |
| `research_db.py` | 研究 SQLite | 持久層 |
| `trades.py` | 交易記錄 | 04.x 已執行 |
| `trading_rules.py` | 交易規則 | 06 + rules |
| `tw_trading_calendar.py` | 台股交易日曆 | 全域（盤前/盤後判斷） |
| `themes.py` | 主題（顏色等） | **改用 `DESIGN_TOKENS.md`** |
| `config.py` | 設定載入 | 06 |
| `logger.py` | log | 後端基礎 |
| `atomic_json.py` | 原子寫 JSON | 後端基礎 |
| `check_updates.py` | 更新檢查 | StatusBar 版本 |
| `diagnose.py` | 診斷 | 06 about |
| `halt.py` | 停盤判斷 | StatusBar 狀態 |

---

## 2. 建議的 FastAPI 結構

```
backend/
├── app/
│   ├── main.py                    # FastAPI app 入口
│   ├── deps.py                    # 依賴注入（auth, db, shioaji client）
│   ├── routers/
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── predict.py             # 03.x
│   │   ├── daytrade.py            # 04.x
│   │   ├── order.py               # 04.3
│   │   ├── portfolio.py           # 05.1
│   │   ├── journal.py             # 05.2
│   │   ├── chat.py                # 05.2 AI 顧問 (SSE)
│   │   ├── backtest.py            # 05.3
│   │   ├── report.py              # 05.4
│   │   ├── scanner.py             # 05.5
│   │   ├── settings.py            # 06
│   │   └── market.py              # StatusBar + WS
│   ├── ws/
│   │   ├── daytrade.py            # WS /ws/daytrade
│   │   ├── market.py              # WS /ws/market
│   │   └── chart.py               # WS /ws/daytrade/:code/chart
│   ├── schemas/                   # Pydantic models (見 DATA_SHAPES.md)
│   └── services/                  # 包裹既有 ai_stock/* 模組
│       ├── ai_service.py          # 包 ai_client + chat_agent
│       ├── shioaji_service.py     # 包 executor + portfolio
│       ├── monitor_service.py     # 包 monitor_agent + intraday_monitor
│       └── ...
└── ai_stock/                      # 既有模組原封不動
```

**關鍵原則**：
- **不要重寫 `ai_stock/*` 的邏輯**
- FastAPI router 只負責：取請求 → 呼叫 service → 包成 schema → 回傳
- service 層做 import + 薄包裝
- 必要時為既有模組補上 type hints

---

## 3. Router 對應表（路徑 → Python 函式）

### 3.1 `/api/predict/*` → 03.x AI 預測

```python
# backend/app/routers/predict.py
from fastapi import APIRouter
from ai_stock import morning_briefing, deep_analyzer, candidate_builder, risk_guard
from ai_stock.research_db import db
from ..schemas.predict import TopNRun, DeepAnalysis, ReasoningTrace

router = APIRouter(prefix="/api/predict", tags=["predict"])

@router.get("/today", response_model=TopNRun)
async def today():
    """讀取最新 daily_plans + 組合成 TopNRun"""
    run = db.latest_daily_plan()                  # research_db.py
    picks = db.picks_for_run(run["id"])
    risk = risk_guard.audit(picks)                # 既有
    return TopNRun(...).from_db(run, picks, risk)

@router.post("/run", response_model=TopNRun)
async def run():
    """觸發新一輪 PremarketJob (同步等待 18s 或 streaming)"""
    result = morning_briefing.PremarketJob().run()  # 既有
    return TopNRun(...).from_result(result)

@router.get("/{code}", response_model=DeepAnalysis)
async def deep(code: str):
    analysis = deep_analyzer.run_deep_analysis(code)  # 既有
    return DeepAnalysis(...).from_deep(analysis)

@router.get("/{code}/reasoning", response_model=ReasoningTrace)
async def reasoning(code: str, run_id: str | None = None):
    """需要為 ai_client.py 加入 trace 模式，紀錄每步耗時 + tokens"""
    trace = db.get_reasoning_trace(code, run_id)
    return ReasoningTrace(...).from_db(trace)

@router.post("/approve")
async def approve(run_id: str, code: str):
    user_confirm.send_confirmation(code)          # Telegram
    db.mark_pick_approved(run_id, code)
    return {"ok": True}
```

### 3.2 `/api/daytrade/*` → 04.x

```python
# backend/app/routers/daytrade.py
from ai_stock import monitor_agent, intraday_monitor, alerts, risk_guard
from ai_stock import portfolio, shioaji_portfolio

@router.get("/live", response_model=DaytradeLive)
async def live():
    positions = portfolio.load_current_positions()    # research_db
    threads = monitor_agent.MonitorAgent.instance().threads()
    alerts_list = db.recent_alerts(limit=20)
    risk = risk_guard.cockpit_snapshot()
    return DaytradeLive(
        countdown_seconds=intraday_monitor.seconds_to_force_close(),
        positions=positions, threads=threads, alerts=alerts_list, risk=risk, ...
    )

@router.get("/{code}/chart", response_model=ChartView)
async def chart(code: str):
    ticks = intraday_monitor.fetch_ticks(code)
    return ChartView(...).from_ticks(ticks)

@router.post("/close")
async def close(code: str):
    executor.force_stop_loss(api, code, ...)          # executor.py
    return {"ok": True}

@router.post("/close-all")
async def close_all():
    """ForceCloseJob 的手動觸發版"""
    ...
```

### 3.3 `/api/order/*` → 04.3

```python
# backend/app/routers/order.py
from ai_stock import executor, risk_guard, user_confirm

@router.post("/preview", response_model=OrderTicket)
async def preview(req: OrderRequest):
    checks = risk_guard.run_all_checks(req)
    return OrderTicket(...).build(req, checks)

@router.post("/submit", response_model=OrderResult)
async def submit(ticket: OrderTicket):
    # 1. 風控 final check
    if not all(c.status != "fail" for c in ticket.risk_checks):
        return OrderResult(status="rejected", ...)
    # 2. Telegram 二次確認
    msg_id = await user_confirm.send_confirmation_async(ticket)
    confirmed = await user_confirm.wait_for_confirmation(msg_id, timeout=60)
    if not confirmed:
        return OrderResult(status="cancelled", ...)
    # 3. 真正下單
    result = executor.place_stock_order(...)          # 既有
    return OrderResult(...).from_result(result)
```

### 3.4 `/api/portfolio` → 05.1

```python
from ai_stock import portfolio, daily_tracker

@router.get("/", response_model=PortfolioSummary)
async def summary():
    return PortfolioSummary(
        net_value=portfolio.net_value(),
        positions=portfolio.load_current_positions(),
        sector_breakdown=portfolio.sector_breakdown(),
        recent_pnl_days=daily_tracker.last_n_days(14),
        cumulative_vs_index=daily_tracker.month_to_date_curve(),
        ...
    )
```

### 3.5 `/api/journal` + `/api/chat` → 05.2

```python
from ai_stock import learning_db, chat_agent

@router.get("/journal")
async def journal_list():
    return learning_db.recent_entries(limit=50)

@router.post("/chat")
async def chat(req: ChatRequest):
    """SSE streaming response"""
    async def stream():
        async for chunk in chat_agent.stream(req.messages, model="sonnet"):
            yield f"data: {chunk}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")
```

### 3.6 `/api/backtest` → 05.3

```python
from ai_stock import simulate, sim_engine

@router.post("/", response_model=BacktestResult)
async def run_backtest(params: BacktestParams):
    result = sim_engine.run(
        start=params.start, end=params.end,
        strategy=params.strategy, slippage=params.slippage,
    )
    return BacktestResult(...).from_sim(result)
```

### 3.7 `/api/report/weekly` → 05.4

```python
from ai_stock import weekly_report, daily_tracker

@router.get("/weekly", response_model=WeeklyReport)
async def weekly(week: str | None = None):
    return weekly_report.build(week or "current")
```

### 3.8 `/api/scanner` → 05.5

```python
from ai_stock import market_scan, news_agent, market_index, futures_premium

@router.get("/", response_model=MarketScan)
async def scan():
    return MarketScan(
        indices=market_index.snapshot(),
        sectors=market_scan.sector_breadth(),
        signals=market_scanner.live_signals(),
        news=news_agent.recent(limit=10),
    )
```

### 3.9 `/api/settings` → 06

```python
from ai_stock import config

@router.get("/", response_model=AppSettings)
async def get_settings():
    return AppSettings.from_env()

@router.patch("/", response_model=AppSettings)
async def patch_settings(patch: SettingsPatch):
    config.update(patch.dict(exclude_unset=True))     # 寫回 .env
    return AppSettings.from_env()

@router.post("/toggle-mode")
async def toggle_mode(req: ToggleModeReq):
    # 二次驗證
    if req.confirm_email != current_user.email:
        raise HTTPException(403)
    config.set("SHIOAJI_SIMULATION", "false" if req.target == "live" else "true")
    return {"ok": True, "mode": req.target}
```

---

## 4. WebSocket 對應

### `/ws/daytrade` → 04.1 駕駛艙 live push

```python
# backend/app/ws/daytrade.py
from ai_stock import monitor_agent, alerts, intraday_monitor

@app.websocket("/ws/daytrade")
async def ws_daytrade(ws: WebSocket):
    await ws.accept()
    # 訂閱 MonitorAgent 的 Queue
    queue = monitor_agent.MonitorAgent.instance().subscribe()
    while True:
        msg = await queue.get()
        await ws.send_json({
            "type": msg.kind,                       # 'tick' | 'alert' | 'thread' | 'countdown'
            "data": msg.payload,
        })
```

**整合既有 `monitor_agent.py`**：
- `monitor_agent.AlertWorker` 已有 Queue 機制
- 新增 `subscribe()` 方法回傳 asyncio.Queue
- `_poll_loop` 每 30 秒推一筆 tick 給所有訂閱者

### `/ws/market` → StatusBar live

```python
@app.websocket("/ws/market")
async def ws_market(ws: WebSocket):
    while True:
        snap = market_index.snapshot()
        await ws.send_json(snap.dict())
        await asyncio.sleep(1)
```

---

## 5. 既有模組需要的擴充

要讓前端跑得起來，這些 Python 模組可能需要小修改：

### 5.1 `ai_client.py` 新增 `trace_call()`
```python
def trace_call(prompt: str, **kwargs) -> AICallTrace:
    """回傳含每步 timing/tokens/cost 的詳細結果，存到 research_db.ai_traces"""
    trace = AICallTrace()
    trace.t_input  = now_ms()
    trace.fetch_input_size = len(prompt)
    trace.t_llm_start = now_ms()
    response = call_haiku(prompt, **kwargs)
    trace.t_llm_end = now_ms()
    trace.tokens_in = response.usage.input_tokens
    trace.tokens_out = response.usage.output_tokens
    trace.cost_usd = compute_cost(...)
    trace.raw = response.content
    db.save_ai_trace(trace)
    return trace
```

### 5.2 `monitor_agent.py` 新增 WS 訂閱
```python
class MonitorAgent:
    _subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def _broadcast(self, msg):
        for q in self._subscribers:
            q.put_nowait(msg)
```

### 5.3 `research_db.py` 新增 ai_traces / ai_marks 表
```sql
CREATE TABLE IF NOT EXISTS ai_traces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT, code TEXT,
  prompt TEXT, response_raw TEXT, response_parsed TEXT,
  tokens_in INT, tokens_out INT, cost_usd REAL,
  duration_ms INT,
  decision_hash TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_marks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT, date TEXT,
  tick_index INT, kind TEXT, label TEXT,
  confidence REAL, reasoning TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5.4 `executor.py` 新增 async 包裝
- 既有同步呼叫保留
- 加一層 `async def place_stock_order_async(...)` 包 `asyncio.to_thread`，避免阻塞 FastAPI

---

## 6. 認證 / Auth

既有專案沒有用戶系統。建議新增最簡 auth：

```python
# backend/app/routers/auth.py
@router.post("/login")
async def login(req: LoginReq):
    user = await verify_password(req.email, req.password)
    if user.two_factor_enabled:
        challenge_id = await send_2fa_code(user)
        return LoginResponse(two_factor_required=True, challenge_id=challenge_id)
    token = create_access_token(user)
    return LoginResponse(user=user, access_token=token, ...)
```

- 單用戶模式：用 `.env` 設定 admin email/password hash
- 多用戶模式：新增 `users` 表 + JWT
- 2FA：TOTP（Google Authenticator）

---

## 7. 部署

### Dev
```bash
# backend
cd backend && uvicorn app.main:app --reload --port 8000

# frontend
cd frontend && pnpm dev    # Vite, port 5173

# Streamlit 舊版（可保留作備援）
streamlit run ai_stock/app.py --server.port 8501
```

### Prod (docker-compose)
```yaml
services:
  backend:
    build: ./backend
    env_file: ai_stock/.env
    volumes: [./ai_stock/data:/app/data]
    ports: ["8000:8000"]
  frontend:
    build: ./frontend
    ports: ["80:80"]   # nginx serve dist + proxy /api to backend
  scheduler:
    build: ./backend
    command: python -m ai_stock.main          # 既有排程器
    env_file: ai_stock/.env
    volumes: [./ai_stock/data:/app/data]
```

---

## 8. 漸進遷移建議

不需要一次性換掉 Streamlit。建議：

1. **Phase 1**：FastAPI 跑在 8000，前端開發新介面
2. **Phase 2**：核心 4 畫面（02、03.1、04.1、06）上線，其他畫面用 iframe 嵌入舊 Streamlit
3. **Phase 3**：補齊全部畫面，淘汰 Streamlit
4. **Phase 4**：手機版 + Telegram bot 整合

---

下一步：讀 `IMPLEMENTATION_PLAN.md`。
