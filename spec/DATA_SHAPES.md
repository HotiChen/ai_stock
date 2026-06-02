# Data Shapes

> 前端期望的資料結構。每一筆都附 TypeScript type + Pydantic model + 來源 Python 模組。
> 你的 AI agent 應建立 FastAPI router 並回傳這些 shape。

---

## 1. 共用基礎型別

```typescript
// shared/types.ts

export type Side = 'buy' | 'sell';
export type Signal = 'buy' | 'sell' | 'hold';
export type LotType = 'common' | 'intraday_odd';
export type ThreadState = 'pending' | 'monitoring' | 'closed_tp' | 'closed_sl' | 'closed_force' | 'rejected';
export type AlertLevel = 'high' | 'med' | 'low';
export type AlertKind = 'target_hit' | 'stop_loss' | 'stop_warn' | 'tp' | 'note' | 'skip';
export type ConfidenceTier = 'high' | 'medium' | 'low'; // ≥0.75 / 0.60-0.74 / <0.60
export type AppMode = 'simulation' | 'live';

export interface Money {
  /** 金額（NTD），整數 */
  amount: number;
  /** 預先格式化的字串，如 "NT$ 142,000" */
  formatted: string;
}
```

```python
# backend/schemas/base.py
from enum import Enum
from pydantic import BaseModel

class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

class Signal(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"

class LotType(str, Enum):
    COMMON = "common"
    INTRADAY_ODD = "intraday_odd"

class ThreadState(str, Enum):
    PENDING = "pending"
    MONITORING = "monitoring"
    CLOSED_TP = "closed_tp"
    CLOSED_SL = "closed_sl"
    CLOSED_FORCE = "closed_force"
    REJECTED = "rejected"

class AlertLevel(str, Enum):
    HIGH = "high"
    MED = "med"
    LOW = "low"

class AppMode(str, Enum):
    SIMULATION = "simulation"
    LIVE = "live"
```

---

## 2. 03.1 AI 預測 — Top N 候選股

### TypeScript

```typescript
// features/predict/types.ts

export interface Pick {
  code: string;            // "2330"
  name: string;            // "台積電"
  sector: string;          // "半導體"
  signal: Signal;          // "buy" | "hold" | ...
  confidence: number;      // 0..1
  target_price: number;    // 1185
  stop_loss_price: number; // 1098
  last_price: number;      // 1135
  change_pct: number;      // +1.34
  spark: number[];         // 最近 10–20 個收盤
  reason: string;          // AI 一句話理由（繁中 ≤80 字）
  tags: string[];          // ['黃金交叉','量比 1.8x','外資買超']
  action: 'approved' | 'pending' | 'rejected' | 'observe';
  budget: number;          // 建議部位金額
  budget_ratio: number;    // 0..1，相對 BUDGET
  run_id: string;          // "plan-2026-05-23-2814"
  created_at: string;      // ISO 8601
}

export interface RiskCheck {
  key: string;             // "重複委託防護" 等
  sub: string;             // "executor.is_duplicate_order()"
  status: 'pass' | 'warn' | 'fail';
  detail: string;          // 顯示在右側的細節
}

export interface SectorAllocation {
  name: string;
  ratio: number;           // 0..1
  limit: number;           // 0..1（MAX_SECTOR_RATIO）
  value: number;           // NTD
}

export interface TopNRun {
  run_id: string;
  date: string;            // "2026-05-23"
  scanned: number;         // 候選股數
  analyzed: number;        // 深度分析數
  buy_signals: number;
  hold_signals: number;
  approved: number;
  rejected_by_risk: number;
  picks: Pick[];
  risk_checks: RiskCheck[];
  sector_allocation: SectorAllocation[];
  blacklist: string[];
  cost: {
    duration_ms: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    model: string;         // "claude-haiku-4-5-20251001"
  };
  telegram_sent_at: string | null;
}
```

### Pydantic

```python
# backend/schemas/predict.py
from typing import Literal
from pydantic import BaseModel

class Pick(BaseModel):
    code: str
    name: str
    sector: str
    signal: Signal
    confidence: float          # 0..1
    target_price: float
    stop_loss_price: float
    last_price: float
    change_pct: float
    spark: list[float]
    reason: str
    tags: list[str]
    action: Literal['approved','pending','rejected','observe']
    budget: int
    budget_ratio: float
    run_id: str
    created_at: str

class RiskCheck(BaseModel):
    key: str
    sub: str
    status: Literal['pass','warn','fail']
    detail: str

class SectorAllocation(BaseModel):
    name: str
    ratio: float
    limit: float
    value: int

class TopNRun(BaseModel):
    run_id: str
    date: str
    scanned: int
    analyzed: int
    buy_signals: int
    hold_signals: int
    approved: int
    rejected_by_risk: int
    picks: list[Pick]
    risk_checks: list[RiskCheck]
    sector_allocation: list[SectorAllocation]
    blacklist: list[str]
    cost: dict
    telegram_sent_at: str | None
```

### 來源

- `morning_briefing.py` → `PremarketJob` 結果
- `candidate_builder.py` → 候選股清單
- `deep_analyzer.py` → 每檔 `DeepAnalysis`
- `risk_guard.py.validate_plan()` → checks + sector_allocation
- `research_db.py.daily_plans` 表

### API

```
GET /api/predict/today                → TopNRun
GET /api/predict/run/:run_id          → TopNRun
POST /api/predict/run                 → 立即執行新一輪
POST /api/predict/approve { run_id, code }    → Pick (action=approved)
POST /api/predict/reject  { run_id, code }    → Pick (action=rejected)
POST /api/predict/approve-all { run_id }      → TopNRun
```

---

## 3. 03.2 個股深度分析

```typescript
export interface Indicator {
  key: string;             // "MA5"
  value: number | string;  // 1126.4 or "中軌"
  hint: string;            // "上彎"
  weight: number;          // +2 / -1
  signal: 'bull' | 'bear' | 'neutral';
}

export interface Scenario {
  name: string;            // "基準情境"
  probability: number;     // 0..1
  target_price: number;
  return_pct: number;      // +0.044
  description: string;
}

export interface DeepAnalysis {
  code: string;
  name: string;
  sector: string;
  // quote snapshot
  last: number; change: number; change_pct: number;
  open: number; high: number; low: number;
  prev_close: number;
  volume_lots: number;     // 張數
  // verdict
  signal: Signal;
  confidence: number;
  target_price: number;
  stop_loss_price: number;
  expected_return: number; // 0..1 (signed)
  max_loss: number;        // signed
  risk_reward: string;     // "1 : 1.33"
  // budget
  budget: number;
  // analysis
  indicators: Indicator[];
  total_score: number;     // sum of weights
  recommendation: string;  // "強力買進" / "買進" / "觀察" / ...
  scenarios: Scenario[];
  ai_conclusion: string;   // 多行字串
  // intraday tick data
  ticks: Tick[];
  // meta
  model: string;
  generated_at: string;    // ISO
}

export interface Tick {
  t: string;               // "09:00"
  open: number; high: number; low: number; close: number;
  volume: number;
}
```

### 來源

- `deep_analyzer.py.run_deep_analysis(code)` → 主結果
- `technical_indicators.py.fetch_indicators(code)` → indicators
- `rules.py.evaluate_signals()` → total_score + signals
- `daytrading_analyzer.py` → ticks

### API

```
GET /api/predict/:code                → DeepAnalysis
POST /api/predict/:code/rerun         → DeepAnalysis
```

---

## 4. 03.3 推理過程 trace

```typescript
export type TracePhase = 'INPUT' | 'FETCH' | 'EVAL' | 'PROMPT' | 'LLM' | 'PARSE' | 'GUARD' | 'OUTPUT';

export interface TraceStep {
  phase: TracePhase;
  t: string;               // "08:30:05"
  label: string;
  body: string;            // 多行細節
  cost_ms: number | null;
  cost_usd?: number;
}

export interface Contribution {
  key: string;             // "技術指標總分"
  detail: string;          // "+15 分"
  delta: number;           // -1..+1 對信心的影響
  kind: 'positive' | 'negative' | 'base';
}

export interface SelfCheck {
  question: string;
  answer: string;
  passed: boolean;
}

export interface ReasoningTrace {
  run_id: string;
  code: string;
  total_duration_ms: number;
  steps: TraceStep[];
  prompt: {
    system: string;
    user: string;
    tokens_in: number;
  };
  response: {
    raw: string;
    parsed: {
      signal: Signal;
      confidence: number;
      target_price: number;
      stop_loss_price: number;
      reason: string;
    };
    tokens_out: number;
  };
  contributions: Contribution[];   // 信心構成
  final_confidence: number;
  self_check: SelfCheck[];
  decision_hash: string;           // for audit
}
```

### 來源

- `ai_client.py.call_haiku()` → 加入 trace 模式，記錄每步 timestamp + cost
- `ai_client.py.build_safe_prompt()` → prompt section
- `risk_guard.py.validate_plan()` → GUARD step
- 建議 **新增** `ai_client.py.trace_call()` wrapper，把每次 LLM 呼叫紀錄到 `research_db.ai_traces` 表

### API

```
GET /api/predict/:code/reasoning?run_id=...   → ReasoningTrace
GET /api/predict/run/:run_id/reasoning        → ReasoningTrace[] (整批)
```

---

## 5. 04.1 駕駛艙 (Daytrade Live)

```typescript
export interface Position {
  code: string;
  name: string;
  sector: string;
  side: Side;
  entry_price: number;
  last_price: number;
  quantity: number;
  lot: LotType;
  cost: number;
  market_value: number;
  pnl: number;
  pnl_pct: number;
  target_price: number;
  stop_loss_price: number;
  distance_to_tp_pct: number;
  distance_to_sl_pct: number;
  thread_state: ThreadState;
  confidence: number;
  opened_at: string;
}

export interface Alert {
  id: string;
  time: string;            // "10:41:55"
  level: AlertLevel;
  code: string;
  name: string;
  text: string;
  kind: AlertKind;
  resolved: boolean;
  source: string;          // "MonitorAgent"
  telegram_sent: boolean;
}

export interface StrategyThread {
  code: string;
  name: string;
  state: ThreadState;
  last_tick_at: string;
  age_seconds: number;
  target_price: number;
  stop_loss_price: number;
  distance_label: string;  // "+4.4% / -3.3%" 或 "達成"
  poll_count: number;
  alert_count: number;
}

export interface RiskCockpit {
  budget: number;
  used: number;
  free: number;
  utilization: number;     // 0..1
  intraday_pnl: number;
  intraday_pnl_pct: number;
  daily_max_dd_limit: number;       // 例 -0.03
  sector_allocation: SectorAllocation[];
  blacklist: string[];
  single_max: { value: number; ratio: number; limit: number; ok: boolean };
}

export interface DaytradeLive {
  /** 距 13:25 強制平倉的秒數 */
  countdown_seconds: number;
  force_close_at: string;  // "13:25:00"
  monitoring_count: number;
  closed_count: number;
  unrealized_pnl: number;
  realized_pnl: number;
  net_pnl: number;
  net_value: number;
  positions: Position[];
  alerts: Alert[];
  threads: StrategyThread[];
  risk: RiskCockpit;
  next_poll_in_seconds: number;
}
```

### 來源

- `monitor_agent.py.MonitorAgent` → positions + threads + alerts
- `intraday_monitor.py` → tick polling
- `alerts.py` + `research_db.alerts` 表 → alerts
- `risk_guard.py` → risk cockpit
- `portfolio.py` / `shioaji_portfolio.py` → positions

### API + WebSocket

```
GET  /api/daytrade/live                   → DaytradeLive (初始 snapshot)
WS   /ws/daytrade                          → 即時推送
  - {type: 'tick',     position: Position}
  - {type: 'alert',    alert: Alert}
  - {type: 'thread',   thread: StrategyThread}
  - {type: 'countdown', seconds: number}
POST /api/daytrade/close-all              → 全部平倉
POST /api/daytrade/close { code }         → 單檔平倉
POST /api/daytrade/adjust-tp { code, value }
POST /api/daytrade/adjust-sl { code, value }
POST /api/daytrade/mute { minutes }       → 暫時靜音警報
```

---

## 6. 04.2 K 線 + AI 標記

```typescript
export interface AIMark {
  index: number;           // tick index
  time: string;            // "09:00"
  kind: 'buy' | 'sell' | 'add' | 'reduce' | 'warn' | 'tp' | 'sl' | 'note';
  label: string;
  confidence: number;
  reasoning?: string;      // optional expanded
}

export interface ChartView {
  code: string;
  ticks: Tick[];
  ma20: number[];
  ma5: number[];
  bollinger: { upper: number[]; mid: number[]; lower: number[] };
  rsi: number[];
  ai_marks: AIMark[];
  next_action_suggestion?: {
    kind: 'adjust_tp' | 'adjust_sl' | 'reduce' | 'add' | 'close' | 'hold';
    text: string;
    confidence: number;
    suggested_value?: number;
  };
}
```

### 來源

- `daytrading_analyzer.py` → ticks + indicators
- `monitor_agent.py` → ai_marks（每次 AI 建議寫入 `research_db.ai_marks` 表，建議新增）

### API

```
GET /api/daytrade/:code/chart            → ChartView
WS  /ws/daytrade/:code/chart             → push new ticks + new AI marks
```

---

## 7. 04.3 下單流程

```typescript
export interface OrderTicket {
  code: string;
  name: string;
  last_price: number;
  side: Side;
  lot: LotType;
  price_type: 'LMT' | 'MKT';
  price: number;
  quantity: number;
  amount: number;
  target_price: number;
  stop_loss_price: number;
  source: {
    type: 'ai' | 'manual';
    run_id?: string;
    confidence?: number;
    reason?: string;
    model?: string;
  };
  risk_checks: RiskCheck[];   // 6 項
  mode: AppMode;
  dry_run_preview: string;    // 多行 code preview
}

export interface OrderResult {
  order_id: string;
  status: 'submitted' | 'filled' | 'rejected' | 'cancelled';
  filled_at?: string;
  filled_price?: number;
  filled_amount?: number;
  rejection_reason?: string;
  telegram_message_id?: string;
}
```

### 來源

- `executor.py.place_stock_order()`
- `risk_guard.py.validate_plan()`
- `user_confirm.py.send_confirmation()`

### API

```
POST /api/order/preview { ... }          → OrderTicket
POST /api/order/submit  { ticket }       → OrderResult
POST /api/order/confirm-telegram { ... } → OrderResult
GET  /api/order/today                    → Trade[]
```

---

## 8. 04.x 已執行交易

```typescript
export interface Trade {
  id: number;
  time: string;            // "09:00:12"
  date: string;
  code: string;
  name: string;
  side: Side;
  quantity: number;
  price: number;
  amount: number;
  lot: LotType;
  status: 'filled' | 'cancelled' | 'partial';
  pnl?: number;            // 若為 sell，已實現
  reason?: string;         // "目標價達成" / "AI 觸發停利"
  order_id: string;
  sector: string;
}
```

### 來源

- `research_db.daily_trades` 表
- `trades.py`

### API

```
GET /api/trades?date=YYYY-MM-DD    → Trade[]
GET /api/trades/range?start&end    → Trade[]
```

---

## 9. 05.1 持倉 / 投資組合

```typescript
export interface PortfolioSummary {
  net_value: number;
  budget: number;
  free_cash: number;
  cash_ratio: number;             // free/budget
  unrealized_pnl: number;
  realized_pnl: number;
  net_pnl: number;
  net_pnl_pct: number;
  position_count: number;
  closed_today: number;
  countdown_seconds: number;
  positions: Position[];
  sector_breakdown: SectorAllocation[];
  recent_pnl_days: { date: string; pnl: number }[];    // 14 天
  cumulative_vs_index: { dates: string[]; me: number[]; index: number[] };
  alpha_mtd: number;
}
```

### 來源

- `portfolio.py` `shioaji_portfolio.py`
- `daily_tracker.py`

---

## 10. 05.2 學習日誌

```typescript
export interface JournalEntry {
  id: number;
  date: string;
  code: string;
  name: string;
  pnl: number;
  lesson: string;          // AI 寫的學習摘要
  rule_updated: boolean;   // 是否已套用到 rules.py
  tags: string[];
  related_trade_id?: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  ts: string;
  model?: string;          // "claude-sonnet-4-6"
  tool_calls?: any[];
}
```

### 來源

- `learning_db.py` `learning_report.py`
- `chat_agent.py`

### API

```
GET  /api/journal                  → JournalEntry[]
POST /api/journal                  → JournalEntry (新增)
POST /api/chat                     → ChatMessage (streaming SSE)
GET  /api/chat/history             → ChatMessage[]
```

---

## 11. 05.3 回測

```typescript
export interface BacktestResult {
  id: string;
  range: { start: string; end: string };
  initial_capital: number;
  slippage: number;
  strategy: string;
  trades: number;
  wins: number;
  losses: number;
  winrate: number;
  total_pnl: number;
  total_pnl_pct: number;
  avg_win: number;
  avg_loss: number;
  sharpe: number;
  max_dd: number;
  beat_index_pct: number;
  monthly_returns: number[];       // YoY heatmap
  equity_curve: { date: string; me: number; index: number }[];
  trades_sample: Trade[];          // 前 N 筆
  ai_conclusion: string;
}
```

### 來源

- `simulate.py` `sim_engine.py` `sim_settlement.py`

### API

```
POST /api/backtest { range, strategy, params }    → BacktestResult
GET  /api/backtest/:id                            → BacktestResult
```

---

## 12. 05.4 週報

```typescript
export interface WeeklyReport {
  week_number: number;
  range: { start: string; end: string };
  total_trades: number;
  wins: number;
  losses: number;
  winrate: number;
  net_pnl: number;
  net_pnl_pct: number;
  daily: { date: string; weekday: string; pnl: number; trades: number; wins: number }[];
  highlight: string;       // AI 一段敘事
  by_confidence: { range: string; winrate: number; n: number; pnl: number }[];
  by_sector:     { sector: string; winrate: number; n: number; pnl: number }[];
  next_week_suggestions: { title: string; detail: string }[];
}
```

### 來源

- `weekly_report.py` `weekly_report_runner.py` `daily_tracker.py`

---

## 13. 05.5 大盤掃描

```typescript
export interface MarketIndex {
  key: string;             // "TWII"
  name: string;            // "加權指數"
  value: number;
  change: number;
  change_pct: number;
  volume_label: string;
}

export interface SectorScan {
  sector: string;
  count: number;
  change_pct: number;
  breadth: number;         // 0..1 (上漲家數比例)
  leaders: string[];       // ["2382 廣達", ...]
}

export interface NewsItem {
  source: string;
  time: string;
  headline: string;
  url?: string;
  related_codes?: string[];
}

export interface ScanSignal {
  code: string;
  name: string;
  signal: string;          // "帶量突破半年線"
  confidence: number;
}

export interface MarketScan {
  indices: MarketIndex[];
  sectors: SectorScan[];
  signals: ScanSignal[];
  news: NewsItem[];
}
```

### 來源

- `market_scan.py` `market_scanner.py`
- `news_agent.py`
- `futures_premium.py` `market_index.py`

---

## 14. 06 Settings

```typescript
export interface AppSettings {
  // capital / risk
  budget: number;
  order_hard_limit: number;
  daily_max_loss_pct: number;
  max_position_ratio: number;
  max_sector_ratio: number;
  entry_confidence_threshold: number;
  default_stop_loss_pct: number;
  // AI models
  model_premarket: string;
  model_dashboard: string;
  model_chat: string;
  // execution
  mode: AppMode;
  // notifications
  notify: {
    telegram: { enabled: boolean; chat_id: string; levels: AlertLevel[] };
    email:    { enabled: boolean; address: string; levels: AlertLevel[] };
    desktop:  { enabled: boolean; levels: AlertLevel[] };
    ios_push: { enabled: boolean; device_token: string; levels: AlertLevel[] };
    slack:    { enabled: boolean; webhook: string; levels: AlertLevel[] };
  };
  // misc
  blacklist: string[];
  theme: 'light' | 'dark';
  language: 'zh-TW' | 'zh-CN' | 'en';
  // meta
  api_keys: {
    anthropic: string;        // masked
    shioaji: string;          // masked
    telegram: string;         // masked
  };
  monitor_poll_seconds: number;
  db_path: string;
  version: string;
}
```

### 來源

- `config.py` + `.env`

### API

```
GET  /api/settings                  → AppSettings
PATCH /api/settings                 → AppSettings
POST /api/settings/api-keys         → 驗證並儲存（不回傳明文）
POST /api/settings/toggle-mode      → 切換模擬/真實（需 step-up auth）
POST /api/settings/blacklist        → 增刪黑名單
```

---

## 15. 07 認證

```typescript
export interface User {
  id: string;
  email: string;
  display_name: string;
  shioaji_bound: boolean;
  telegram_bound: boolean;
  two_factor_enabled: boolean;
}

export interface LoginResponse {
  user: User;
  access_token: string;
  refresh_token: string;
  expires_in: number;
  two_factor_required: boolean;
  challenge_id?: string;
}
```

### API

```
POST /api/auth/login         { email, password }
POST /api/auth/verify-2fa    { challenge_id, code }
POST /api/auth/refresh
POST /api/auth/logout
GET  /api/auth/me            → User
```

---

## 16. 全域 — TopBar / StatusBar

```typescript
export interface MarketSnapshot {
  /** 盤中/盤前/盤後/休市 */
  session: 'pre' | 'open' | 'post' | 'closed';
  server_time: string;
  countdown_to_close_seconds: number;
  taiex: { value: number; change: number; change_pct: number };
  otc:   { value: number; change: number; change_pct: number };
  usd_twd: { value: number; change: number };
  api_status: 'ok' | 'degraded' | 'down';
  db_size_mb: number;
  load_ms: number;
}
```

### API + WS

```
GET /api/market/snapshot
WS  /ws/market         → push snapshot 每秒
```

---

## 17. 命名規則總結

- TypeScript field：`snake_case`，與 Python 一致（避免兩端轉換）
- API URL：`/api/{domain}/{resource}/{action}`
- WebSocket topic：`/ws/{domain}` 或 `/ws/{domain}/:id`
- 時間：ISO 8601 字串，前端用 `dayjs` 或 `Intl.DateTimeFormat` 顯示
- 金額：整數 NTD（不要小數），前端負責格式化
- 百分比：傳 `0.034` 或 `+3.4`（決定後固定）；本規格 **建議用 raw 數字**，UI 自行加 `%`

---

下一步：讀 `BACKEND_MAPPING.md`。
