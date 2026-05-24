import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';

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

function formatTime(date: Date): string {
  return date.toLocaleTimeString('zh-TW', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return '已收盤';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${h}h${String(m).padStart(2, '0')}m`;
  }
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function getSecondsToClose(now: Date): number {
  const close = new Date(now);
  close.setHours(13, 25, 0, 0);
  const diff = Math.floor((close.getTime() - now.getTime()) / 1000);
  return diff;
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

  // Clock
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Keyboard shortcuts
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    // Skip if typing in an input
    const tag = (e.target as HTMLElement)?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

    if (e.metaKey || e.ctrlKey) return; // Let cmd+k etc bubble

    switch (e.key.toUpperCase()) {
      case 'D': navigate('/'); break;
      case 'P': navigate('/predict'); break;
      case 'T': navigate('/daytrade'); break;
      case 'H': navigate('/portfolio'); break;
      case 'M': navigate('/scanner'); break;
      case 'B': navigate('/simulate'); break;
      case 'J': navigate('/journal'); break;
      case 'R': navigate('/report'); break;
    }
  }, [navigate]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  const secondsToClose = getSecondsToClose(now);
  const isUrgent = secondsToClose > 0 && secondsToClose < 300; // < 5min

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
            fontSize: 13,
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

        {/* Right: clock + countdown */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '0 16px',
          flexShrink: 0,
        }}>
          {/* Clock */}
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontFeatureSettings: '"tnum" 1, "zero" 1',
            fontSize: 13,
            fontWeight: 500,
            color: 'var(--ink)',
          }}>
            {formatTime(now)}
          </span>

          {/* Countdown */}
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
                收盤
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 12,
                fontWeight: 500,
                color: isUrgent ? 'var(--up)' : 'var(--ink)',
              }}>
                {formatCountdown(secondsToClose)}
              </span>
            </div>
          )}
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
            <StatusDot label="模式" value="模擬" color="var(--gold)" />
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
        <StatusBarItem label="加權" value="—" />
        <StatusBarSep />
        <StatusBarItem label="OTC" value="—" />
        <StatusBarSep />
        <StatusBarItem label="USD/TWD" value="—" />
        <div style={{ flex: 1 }} />
        <StatusBarItem label="API" value="OK" color="var(--down)" />
        <StatusBarSep />
        <StatusBarItem label="DB" value="—MB" />
        <StatusBarSep />
        <StatusBarItem label="LOAD" value="—ms" />
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
