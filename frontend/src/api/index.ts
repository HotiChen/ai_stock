import type {
  AppSettings,
  User,
  LoginResponse,
  TopNRun,
  DeepAnalysis,
  ReasoningTrace,
  DaytradeLive,
  ChartView,
  OrderTicket,
  OrderResult,
  PortfolioSummary,
  JournalEntry,
  ChatMessage,
  BacktestResult,
  BacktestParams,
  WeeklyReport,
  MarketScan,
  MarketSnapshot,
} from '../types';

const BASE = '/api';

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = localStorage.getItem('access_token');
  const res = await fetch(BASE + path, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...opts?.headers,
    },
  });
  if (res.status === 401) {
    localStorage.removeItem('access_token');
    window.location.href = '/login';
    // Throw to stop further processing in the caller
    throw new Error('Unauthorized');
  }
  if (!res.ok) throw new Error(await res.text());

  // 後端在資料庫查不到真實資料時，會回傳寫死的示範資料並標記
  // X-Data-Source: mock（見 backend/app/routers/predict.py、dashboard.py）。
  // 這在下單系統裡必須讓使用者看得見——否則畫面會出現「2330 台積電 信心 0.86」
  // 這種完全捏造、卻又被 Shioaji 真實報價填充過的推薦，無從分辨真假。
  // 把標記掛在回傳物件上（不可列舉，不影響既有欄位與 JSON 序列化）。
  const data = await res.json();
  if (res.headers.get('X-Data-Source') === 'mock' && data && typeof data === 'object') {
    Object.defineProperty(data, '__isMock', { value: true, enumerable: false });
  }
  return data as T;
}

/** 這份資料是後端的示範資料，不是真實紀錄。 */
export function isMockData(data: unknown): boolean {
  return !!data && typeof data === 'object' && (data as { __isMock?: boolean }).__isMock === true;
}

export const api = {
  // Auth
  login: (email: string, password: string) =>
    apiFetch<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  me: () => apiFetch<User>('/auth/me'),

  logout: () =>
    apiFetch<void>('/auth/logout', { method: 'POST' }),

  // Settings
  getSettings: () => apiFetch<AppSettings>('/settings'),

  patchSettings: (patch: Partial<AppSettings>) =>
    apiFetch<AppSettings>('/settings', {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  // Predict
  getTopN: () => apiFetch<TopNRun>('/predict/today'),

  getDeepAnalysis: (code: string) => apiFetch<DeepAnalysis>(`/predict/${code}`),

  getReasoning: (code: string, runId?: string) =>
    apiFetch<ReasoningTrace>(`/predict/${code}/reasoning${runId ? '?run_id=' + runId : ''}`),

  approveRun: (runId: string) =>
    apiFetch<void>('/predict/approve-all', {
      method: 'POST',
      body: JSON.stringify({ run_id: runId }),
    }),

  approvePick: (runId: string, code: string) =>
    apiFetch<void>('/predict/approve', {
      method: 'POST',
      body: JSON.stringify({ run_id: runId, code }),
    }),

  rejectPick: (runId: string, code: string) =>
    apiFetch<void>('/predict/reject', {
      method: 'POST',
      body: JSON.stringify({ run_id: runId, code }),
    }),

  runPremarket: () => apiFetch<TopNRun>('/predict/run', { method: 'POST' }),

  // Daytrade
  getDaytradeLive: () => apiFetch<DaytradeLive>('/daytrade/live'),

  getChartData: (code: string) => apiFetch<ChartView>(`/daytrade/${code}/chart`),

  closePick: (code: string) =>
    apiFetch<void>('/daytrade/close', { method: 'POST', body: JSON.stringify({ code }) }),

  closeAll: () => apiFetch<void>('/daytrade/close-all', { method: 'POST' }),

  adjustTp: (code: string, value: number) =>
    apiFetch<void>('/daytrade/adjust-tp', { method: 'POST', body: JSON.stringify({ code, value }) }),

  adjustSl: (code: string, value: number) =>
    apiFetch<void>('/daytrade/adjust-sl', { method: 'POST', body: JSON.stringify({ code, value }) }),

  previewOrder: (params: Record<string, unknown>) =>
    apiFetch<OrderTicket>('/order/preview', { method: 'POST', body: JSON.stringify(params) }),

  submitOrder: (ticket: OrderTicket) =>
    apiFetch<OrderResult>('/order/submit', { method: 'POST', body: JSON.stringify(ticket) }),

  // Portfolio
  getPortfolio: () => apiFetch<PortfolioSummary>('/portfolio'),

  // Market — 大盤 / 櫃買 / 匯率即時快照（Dashboard 底部狀態列與圖表基準線）
  getMarketSnapshot: () => apiFetch<MarketSnapshot>('/market/snapshot'),

  // Journal
  getJournal: () => apiFetch<JournalEntry[]>('/journal'),

  // Chat (SSE — returns raw Response, not parsed JSON)
  sendChat: (messages: ChatMessage[]) =>
    fetch('/api/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${localStorage.getItem('access_token') ?? ''}`,
      },
      body: JSON.stringify({ messages }),
    }),

  // Backtest
  runBacktest: (params: BacktestParams) =>
    apiFetch<BacktestResult>('/backtest', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  // Weekly Report
  getWeeklyReport: () => apiFetch<WeeklyReport>('/report/weekly'),

  // Scanner
  getScanner: () => apiFetch<MarketScan>('/scanner'),
};
