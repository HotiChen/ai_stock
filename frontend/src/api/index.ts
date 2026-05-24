import type {
  AppSettings,
  User,
  LoginResponse,
  TopNRun,
  DaytradeLive,
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

  // Daytrade
  getDaytradeLive: () => apiFetch<DaytradeLive>('/daytrade/live'),
};
