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
} from '../types';

const BASE = '/api';

/**
 * 帶 HTTP 狀態碼的錯誤。
 *
 * 「今天還沒有選股」和「後端連不上」是兩件完全不同的事，使用者要採取的
 * 行動也不同（等 08:30 / 去看 logs/backend.log）。過去兩者都只是 throw
 * 一個字串，前端無從分辨——於是後端乾脆回假資料，兩個問題一起被蓋掉。
 */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** 404 = 這個資源今天還不存在（不是錯誤，是還沒發生）。 */
export function isNotFound(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

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
  if (!res.ok) {
    const body = await res.text();
    let detail = body;
    try {
      const j = JSON.parse(body);
      detail = j.detail ?? body;
    } catch { /* 非 JSON，用原文 */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
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
