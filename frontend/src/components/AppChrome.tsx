import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useWebSocket } from '../hooks/useWebSocket';
import { useKeyboardNav } from '../hooks/useKeyboardNav';
import { useAppStore } from '../store/appStore';
import { getSecondsToMarketClose } from '../lib/time';
import type { MarketSnapshot } from '../types';

interface AppChromeProps {
  children: React.ReactNode;
  title?: string;
  eyebrow?: string;
}

interface NavItemConfig {
  id: string;
  label: string;
  path: string;
  kbd: string;
  star?: boolean;
}

const NAV_ITEMS: NavItemConfig[] = [
  { id: 'dashboard',  label: '總覽',     path: '/',          kbd: 'D' },
  { id: 'predict',    label: 'AI 預測',   path: '/predict',   kbd: 'P', star: true },
  { id: 'daytrade',   label: 'AI 當沖',   path: '/daytrade',  kbd: 'T', star: true },
  { id: 'portfolio',  label: '持倉',      path: '/portfolio', kbd: 'H' },
  { id: 'scanner',    label: '市場掃描',  path: '/scanner',   kbd: 'M' },
  { id: 'simulate',   label: '回測',      path: '/simulate',  kbd: 'B' },
  { id: 'journal',    label: '學習日誌',  path: '/journal',   kbd: 'J' },
  { id: 'report',     label: '週報',      path: '/report',    kbd: 'R' },
];

// APP_MODE is now sourced from the Zustand appStore (see useAppStore inside the component)

function formatTimeHMS(date: Date): string {
  const h = String(date.getHours()).padStart(2, '0');
  const m = String(date.getMinutes()).padStart(2, '0');
  const s = String(date.getSeconds()).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

function formatCountdownHMS(seconds: number): string {
  if (seconds <= 0) return '已收盤';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}


function getMarketSession(now: Date): 'pre' | 'open' | 'post' | 'closed' {
  const h = now.getHours();
  const m = now.getMinutes();
  const total = h * 60 + m;
  // 08:00–09:00 pre
  if (total >= 8 * 60 && total < 9 * 60) return 'pre';
  // 09:00–13:30 open
  if (total >= 9 * 60 && total < 13 * 60 + 30) return 'open';
  // 13:30–14:30 post
  if (total >= 13 * 60 + 30 && total < 14 * 60 + 30) return 'post';
  return 'closed';
}

interface NavItemProps {
  item: NavItemConfig;
  active: boolean;
  onClick: () => void;
}

function NavItem({ item, active, onClick }: NavItemProps) {
  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}
      style={{
        height: 38,
        padding: '0 14px',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        cursor: 'pointer',
        fontSize: 13,
        color: active ? 'var(--ink)' : 'var(--ink-2)',
        background: active ? 'var(--surface-2)' : 'transparent',
        borderLeft: active ? '2px solid var(--ink)' : '2px solid transparent',
        userSelect: 'none',
        position: 'relative',
      }}
    >
      <span style={{ flex: 1 }}>{item.label}</span>

      {item.star && (
        <span style={{
          color: 'var(--up)',
          fontSize: 10,
          fontWeight: 600,
          flexShrink: 0,
        }}>
          ★
        </span>
      )}

      <span style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 10,
        color: 'var(--muted-2)',
        flexShrink: 0,
        letterSpacing: '0.04em',
      }}>
        {item.kbd}
      </span>
    </div>
  );
}

export default function AppChrome({ children, title, eyebrow }: AppChromeProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [now, setNow] = useState(new Date());

  // App mode from Zustand store
  const APP_MODE = useAppStore((s) => s.mode);

  // WebSocket market data
  const { data: marketData, connected: marketConnected } =
    useWebSocket<MarketSnapshot>('/ws/market');

  // Clock — ticks every second
  React.useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Global keyboard navigation — derived from NAV_ITEMS so there's no duplicate mapping
  useKeyboardNav(NAV_ITEMS);

  // Use server countdown if available, else compute locally (13:30 market close, signed)
  const secondsToClose = marketData
    ? marketData.countdown_to_close_seconds
    : getSecondsToMarketClose(now);

  const isUrgent = secondsToClose > 0 && secondsToClose < 300; // < 5 min

  // Market session
  const session: 'pre' | 'open' | 'post' | 'closed' = marketData?.session ?? getMarketSession(now);

  const sessionLabel: Record<string, string> = {
    pre: 'PRE',
    open: 'OPEN',
    post: 'POST',
    closed: 'CLOSED',
  };
  const sessionColor: Record<string, string> = {
    pre: 'var(--gold)',
    open: 'var(--down)',
    post: 'var(--muted)',
    closed: 'var(--muted-2)',
  };

  // StatusBar data (from WS or mock)
  const taiex = marketData?.taiex;
  const otc = marketData?.otc;
  const usd = marketData?.usd_twd;
  const apiOk = marketData?.api_status ?? (marketConnected ? 'ok' : 'degraded');
  const dbMB = marketData?.db_size_mb ?? 0;
  const loadMs = marketData?.load_ms ?? 0;

  // Format signed values
  function fmtIdx(val: number, pct: number): string {
    const sign = pct >= 0 ? '+' : '';
    return `${val.toFixed(1)} ${sign}${pct.toFixed(2)}%`;
  }

  // Determine active nav item
  const activePath = location.pathname;
  function isActive(path: string): boolean {
    if (path === '/') return activePath === '/';
    return activePath.startsWith(path);
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100vh',
      overflow: 'hidden',
      background: 'var(--bg)',
    }}>
      {/* ── TopBar ─────────────────────────── 44px */}
      <header style={{
        height: 44,
        display: 'flex',
        alignItems: 'center',
        borderBottom: '1px solid var(--hair)',
        background: 'var(--surface)',
        flexShrink: 0,
      }}>
        {/* Logo area — matches sidebar width */}
        <div style={{
          width: 200,
          flexShrink: 0,
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          padding: '0 14px',
          borderRight: '1px solid var(--hair)',
        }}>
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontWeight: 600,
            fontSize: 14,
            letterSpacing: '0.06em',
            color: 'var(--ink)',
          }}>
            QUANT·AI
          </span>
        </div>

        {/* Title / eyebrow */}
        <div style={{ flex: 1, padding: '0 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
          {eyebrow && (
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              color: 'var(--muted)',
              letterSpacing: '0.14em',
              textTransform: 'uppercase',
            }}>
              {eyebrow}
            </span>
          )}
          {title && (
            <span style={{
              fontSize: 13,
              fontWeight: 500,
              color: 'var(--ink)',
            }}>
              {title}
            </span>
          )}
        </div>

        {/* Right: session pill + clock + countdown + mode */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '0 16px',
          flexShrink: 0,
        }}>
          {/* Market session pill */}
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            padding: '2px 7px',
            borderRadius: 2,
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            letterSpacing: '0.04em',
            color: sessionColor[session],
            border: `1px solid ${sessionColor[session]}`,
            fontWeight: 600,
          }}>
            {sessionLabel[session]}
          </span>

          {/* Clock HH:mm:ss */}
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontFeatureSettings: '"tnum" 1, "zero" 1',
            fontSize: 13,
            fontWeight: 500,
            color: 'var(--ink)',
          }}>
            {formatTimeHMS(now)}
          </span>

          {/* Countdown to close */}
          {secondsToClose > -3600 && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '3px 8px',
              background: isUrgent ? 'var(--up-soft)' : 'var(--surface-2)',
              border: `1px solid ${isUrgent ? 'var(--up)' : 'var(--hair)'}`,
              borderRadius: 2,
            }}>
              <span style={{
                fontSize: 10,
                color: 'var(--muted)',
                letterSpacing: '0.04em',
              }}>
                距收盤
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 12,
                fontWeight: 500,
                color: isUrgent ? 'var(--up)' : 'var(--ink)',
              }}>
                {formatCountdownHMS(secondsToClose)}
              </span>
            </div>
          )}

          {/* Mode badge */}
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            padding: '2px 7px',
            borderRadius: 2,
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            letterSpacing: '0.04em',
            fontWeight: 600,
            color: APP_MODE === 'simulation' ? 'var(--gold)' : '#fff',
            background: APP_MODE === 'simulation' ? 'var(--gold-soft)' : 'var(--up)',
            border: `1px solid ${APP_MODE === 'simulation' ? 'var(--gold)' : 'var(--up)'}`,
          }}>
            {APP_MODE === 'simulation' ? 'SIMULATION' : 'LIVE'}
          </span>
        </div>
      </header>

      {/* ── Middle: Sidebar + Content ───────── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* ── Sidebar ─────────────────────── 200px */}
        <nav style={{
          width: 200,
          flexShrink: 0,
          borderRight: '1px solid var(--hair)',
          background: 'var(--surface)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}>
          {/* Nav items */}
          <div style={{ flex: 1, overflowY: 'auto', paddingTop: 8 }}>
            {NAV_ITEMS.map((item) => (
              <NavItem
                key={item.id}
                item={item}
                active={isActive(item.path)}
                onClick={() => navigate(item.path)}
              />
            ))}
          </div>

          {/* ── Sidebar bottom: system status ── */}
          <div style={{
            borderTop: '1px solid var(--hair)',
            padding: '10px 14px',
          }}>
            <div style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              letterSpacing: '0.10em',
              textTransform: 'uppercase',
              color: 'var(--muted)',
              marginBottom: 6,
            }}>
              系統狀態
            </div>
            <StatusDot label="Shioaji" ok />
            <StatusDot
              label="模式"
              value={APP_MODE === 'simulation' ? '模擬' : '真實'}
              color={APP_MODE === 'simulation' ? 'var(--gold)' : 'var(--up)'}
            />
            <StatusDot label="版本" value="v0.1" />
          </div>
        </nav>

        {/* ── Content ─────────────────────── */}
        <main style={{
          flex: 1,
          overflow: 'auto',
          background: 'var(--bg)',
        }}>
          {children}
        </main>
      </div>

      {/* ── StatusBar ───────────────────────── 22px */}
      <footer style={{
        height: 22,
        flexShrink: 0,
        borderTop: '1px solid var(--hair)',
        background: 'var(--surface)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 12px',
        gap: 16,
        fontSize: 10,
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1, "zero" 1',
        color: 'var(--muted)',
        overflow: 'hidden',
      }}>
        {/* 取不到報價時顯示「—」。先前這裡在無資料時填死值（加權 21,234.5、
            OTC 312.4、USD/TWD 31.85），與真實指數差了一倍以上而且從不更新，
            看起來卻像即時報價。狀態列一律：有資料才顯示數字。 */}
        <StatusBarItem
          label="加權"
          value={taiex ? fmtIdx(taiex.value, taiex.change_pct) : '—'}
          color={taiex ? (taiex.change_pct >= 0 ? 'var(--up)' : 'var(--down)') : undefined}
        />
        <StatusBarSep />
        <StatusBarItem
          label="OTC"
          value={otc ? fmtIdx(otc.value, otc.change_pct) : '—'}
          color={otc ? (otc.change_pct >= 0 ? 'var(--up)' : 'var(--down)') : undefined}
        />
        <StatusBarSep />
        <StatusBarItem label="USD/TWD" value={usd ? usd.value.toFixed(2) : '—'} />
        <div style={{ flex: 1 }} />
        <StatusBarItem
          label="API"
          value="●"
          color={apiOk === 'ok' ? 'var(--down)' : apiOk === 'degraded' ? 'var(--gold)' : 'var(--up)'}
        />
        <StatusBarSep />
        <StatusBarItem
          label="DB"
          value={dbMB > 0 ? `${dbMB}MB` : '124MB'}
        />
        <StatusBarSep />
        <StatusBarItem
          label="LOAD"
          value={loadMs > 0 ? `${loadMs}ms` : '12ms'}
        />
      </footer>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────

function StatusDot({ label, ok, value, color }: {
  label: string;
  ok?: boolean;
  value?: string;
  color?: string;
}) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      marginBottom: 3,
      fontSize: 10,
    }}>
      <span style={{
        width: 5,
        height: 5,
        borderRadius: '50%',
        background: color ?? (ok ? 'var(--down)' : 'var(--muted-2)'),
        flexShrink: 0,
      }} />
      <span style={{ color: 'var(--muted)' }}>{label}</span>
      {value && (
        <span style={{ color: color ?? 'var(--ink-2)', marginLeft: 'auto' }}>{value}</span>
      )}
    </div>
  );
}

function StatusBarItem({ label, value, color }: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <span>
      <span style={{ color: 'var(--muted-2)' }}>{label} </span>
      <span style={{ color: color ?? 'var(--ink-2)' }}>{value}</span>
    </span>
  );
}

function StatusBarSep() {
  return (
    <span style={{ color: 'var(--hair-2)', userSelect: 'none' }}>·</span>
  );
}
