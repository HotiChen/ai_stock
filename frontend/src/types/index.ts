// ============================================================
// Shared base types
// ============================================================

export type Side = 'buy' | 'sell';
export type Signal = 'buy' | 'sell' | 'hold';
export type LotType = 'common' | 'intraday_odd';
export type ThreadState =
  | 'pending'
  | 'monitoring'
  | 'closed_tp'
  | 'closed_sl'
  | 'closed_force'
  | 'rejected';
export type AlertLevel = 'high' | 'med' | 'low';
export type AlertKind =
  | 'target_hit'
  | 'stop_loss'
  | 'stop_warn'
  | 'tp'
  | 'note'
  | 'skip';
export type ConfidenceTier = 'high' | 'medium' | 'low'; // ≥0.75 / 0.60-0.74 / <0.60
export type AppMode = 'simulation' | 'live';

export interface Money {
  /** 金額（NTD），整數 */
  amount: number;
  /** 預先格式化的字串，如 "NT$ 142,000" */
  formatted: string;
}

// ============================================================
// 03.1 AI 預測 — Top N 候選股
// ============================================================

export interface Pick {
  code: string;            // "2330"
  name: string;            // "台積電"
  sector: string;          // "半導體"
  signal: Signal;
  confidence: number;      // 0..1
  target_price: number;
  stop_loss_price: number;
  last_price: number;
  change_pct: number;      // +1.34
  spark: number[];         // 最近 10–20 個收盤
  reason: string;          // AI 一句話理由
  tags: string[];
  action: 'approved' | 'pending' | 'rejected' | 'observe';
  budget: number;
  budget_ratio: number;    // 0..1
  run_id: string;
  created_at: string;      // ISO 8601
}

export interface RiskCheck {
  key: string;
  sub: string;
  status: 'pass' | 'warn' | 'fail';
  detail: string;
}

export interface SectorAllocation {
  name: string;
  ratio: number;           // 0..1
  limit: number;           // 0..1
  value: number;           // NTD
}

export interface TopNRun {
  run_id: string;
  date: string;            // "2026-05-23"
  scanned: number;
  analyzed: number;
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
    model: string;
  };
  telegram_sent_at: string | null;
}

// ============================================================
// 03.2 個股深度分析
// ============================================================

export interface Indicator {
  key: string;
  value: number | string;
  hint: string;
  weight: number;
  signal: 'bull' | 'bear' | 'neutral';
}

export interface Scenario {
  name: string;
  probability: number;     // 0..1
  target_price: number;
  return_pct: number;
  description: string;
}

export interface Tick {
  t: string;               // "09:00"
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface DeepAnalysis {
  code: string;
  name: string;
  sector: string;
  // quote snapshot
  last: number;
  change: number;
  change_pct: number;
  open: number;
  high: number;
  low: number;
  prev_close: number;
  volume_lots: number;
  // verdict
  signal: Signal;
  confidence: number;
  target_price: number;
  stop_loss_price: number;
  expected_return: number;
  max_loss: number;
  risk_reward: string;
  // budget
  budget: number;
  // analysis
  indicators: Indicator[];
  total_score: number;
  recommendation: string;
  scenarios: Scenario[];
  ai_conclusion: string;
  // intraday tick data
  ticks: Tick[];
  // meta
  model: string;
  generated_at: string;    // ISO
}

// ============================================================
// 03.3 推理過程 trace
// ============================================================

export type TracePhase =
  | 'INPUT'
  | 'FETCH'
  | 'EVAL'
  | 'PROMPT'
  | 'LLM'
  | 'PARSE'
  | 'GUARD'
  | 'OUTPUT';

export interface TraceStep {
  phase: TracePhase;
  t: string;               // "08:30:05"
  label: string;
  body: string;
  cost_ms: number | null;
  cost_usd?: number;
}

export interface Contribution {
  key: string;
  detail: string;
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
  contributions: Contribution[];
  final_confidence: number;
  self_check: SelfCheck[];
  decision_hash: string;
}

// ============================================================
// 04.1 駕駛艙 (Daytrade Live)
// ============================================================

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
  source: string;
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
  distance_label: string;
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
  daily_max_dd_limit: number;
  sector_allocation: SectorAllocation[];
  blacklist: string[];
  single_max: { value: number; ratio: number; limit: number; ok: boolean };
}

export interface DaytradeLive {
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

// ============================================================
// 04.2 K 線 + AI 標記
// ============================================================

export interface AIMark {
  index: number;
  time: string;
  kind: 'buy' | 'sell' | 'add' | 'reduce' | 'warn' | 'tp' | 'sl' | 'note';
  label: string;
  confidence: number;
  reasoning?: string;
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

// ============================================================
// 04.3 下單流程
// ============================================================

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
  risk_checks: RiskCheck[];
  mode: AppMode;
  dry_run_preview: string;
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

// ============================================================
// 04.x 已執行交易
// ============================================================

export interface Trade {
  id: number;
  time: string;
  date: string;
  code: string;
  name: string;
  side: Side;
  quantity: number;
  price: number;
  amount: number;
  lot: LotType;
  status: 'filled' | 'cancelled' | 'partial';
  pnl?: number;
  reason?: string;
  order_id: string;
  sector: string;
}

// ============================================================
// 05.1 持倉 / 投資組合
// ============================================================

export interface PortfolioSummary {
  net_value: number;
  budget: number;
  free_cash: number;
  cash_ratio: number;
  unrealized_pnl: number;
  realized_pnl: number;
  net_pnl: number;
  net_pnl_pct: number;
  position_count: number;
  closed_today: number;
  countdown_seconds: number;
  positions: Position[];
  sector_breakdown: SectorAllocation[];
  recent_pnl_days: { date: string; pnl: number }[];
  cumulative_vs_index: { dates: string[]; me: number[]; index: number[] };
  alpha_mtd: number;
}

// ============================================================
// 05.2 學習日誌
// ============================================================

export interface JournalEntry {
  id: number;
  date: string;
  code: string;
  name: string;
  pnl: number;
  lesson: string;
  rule_updated: boolean;
  tags: string[];
  related_trade_id?: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  ts: string;
  model?: string;
  tool_calls?: unknown[];
}

// ============================================================
// 05.3 回測
// ============================================================

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
  monthly_returns: number[];
  equity_curve: { date: string; me: number; index: number }[];
  trades_sample: Trade[];
  ai_conclusion: string;
}

// ============================================================
// 06 Settings
// ============================================================

export interface AppSettings {
  budget: number;
  order_hard_limit: number;
  daily_max_loss_pct: number;
  max_position_ratio: number;
  max_sector_ratio: number;
  entry_confidence_threshold: number;
  default_stop_loss_pct: number;
  model_premarket: string;
  model_dashboard: string;
  model_chat: string;
  mode: AppMode;
  notify: {
    telegram: { enabled: boolean; chat_id: string; levels: AlertLevel[] };
    email:    { enabled: boolean; address: string; levels: AlertLevel[] };
    desktop:  { enabled: boolean; levels: AlertLevel[] };
    ios_push: { enabled: boolean; device_token: string; levels: AlertLevel[] };
    slack:    { enabled: boolean; webhook: string; levels: AlertLevel[] };
  };
  blacklist: string[];
  theme: 'light' | 'dark';
  language: 'zh-TW' | 'zh-CN' | 'en';
  api_keys: {
    anthropic: string;
    shioaji: string;
    telegram: string;
  };
  monitor_poll_seconds: number;
  db_path: string;
  version: string;
}

// ============================================================
// 07 認證
// ============================================================

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

// ============================================================
// 16. 全域 — TopBar / StatusBar
// ============================================================

export interface MarketSnapshot {
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

// ============================================================
// 05.4 週報
// ============================================================

export interface DailyDetail {
  date: string;         // "2026-05-26"
  weekday: string;      // "一"
  pnl: number;
  trades: number;
  winrate: number;
}

export interface WeeklyReport {
  week_label: string;   // "Week 21 · 2026"
  narrative: string;    // AI 敘事段落
  net_pnl: number;
  net_pnl_pct: number;
  winrate: number;
  trades: number;
  best_trade_pnl: number;
  avg_daily_pnl: number;
  daily_details: DailyDetail[];
  confidence_winrate: { tier: string; winrate: number; trades: number }[];
  sector_winrate: { sector: string; winrate: number; trades: number }[];
  next_week_suggestions: string[];
}

// ============================================================
// 05.5 大盤掃描
// ============================================================

export interface SectorHeat {
  name: string;
  count: number;
  change_pct: number;
  up_count: number;
  down_count: number;
  leaders: string[];   // code list
}

export interface NewsItem {
  id: string;
  source: string;
  time: string;
  headline: string;
  url?: string;
}

export interface IndexData {
  name: string;
  value: number;
  change: number;
  change_pct: number;
}

export interface MarketScan {
  indices: {
    taiex: IndexData;
    otc: IndexData;
    tsmc_adr: IndexData;
    usd_twd: IndexData;
    vix: IndexData;
    futures: IndexData;
  };
  sectors: SectorHeat[];
  ai_signals: Pick[];
  news: NewsItem[];
}

// ============================================================
// 05.3 回測參數
// ============================================================

export interface BacktestParams {
  start: string;
  end: string;
  strategy: string;
  initial_capital: number;
  slippage: number;
}
