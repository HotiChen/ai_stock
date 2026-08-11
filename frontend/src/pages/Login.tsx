// 01 Login — Wave 2-E
import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import Button from '../components/Button';
import { api } from '../api';

// ──────────────────────────────────────────────
// SVG K-Bar decoration (48 bars, alternating red/green)
// ──────────────────────────────────────────────
const KBAR_COUNT = 48;
const KBAR_W = 6;
const KBAR_GAP = 4;
const KBAR_AREA_W = KBAR_COUNT * (KBAR_W + KBAR_GAP);
const KBAR_AREA_H = 120;

// Deterministic pseudo-random heights for the K-bars
function pseudoRand(seed: number): number {
  const x = Math.sin(seed + 1) * 10000;
  return x - Math.floor(x);
}

function KBarDecoration() {
  const bars = Array.from({ length: KBAR_COUNT }, (_, i) => {
    const isUp = i % 3 !== 2; // mostly up, some down — alternating feel
    const totalH = 20 + pseudoRand(i * 7) * 80;
    const bodyFrac = 0.4 + pseudoRand(i * 13) * 0.4;
    const bodyH = totalH * bodyFrac;
    const wickH = totalH - bodyH;
    const wickTop = pseudoRand(i * 17) * wickH;
    const wickBot = wickH - wickTop;

    const x = i * (KBAR_W + KBAR_GAP);
    const bodyTop = KBAR_AREA_H - bodyH - wickBot;
    const color = isUp ? 'var(--up)' : 'var(--down)';

    return (
      <g key={i}>
        {/* Wick */}
        <line
          x1={x + KBAR_W / 2} y1={bodyTop - wickTop}
          x2={x + KBAR_W / 2} y2={bodyTop + bodyH + wickBot}
          stroke={color} strokeWidth={1}
        />
        {/* Body */}
        <rect
          x={x} y={bodyTop}
          width={KBAR_W} height={Math.max(bodyH, 2)}
          fill={color}
        />
      </g>
    );
  });

  return (
    <svg
      width={KBAR_AREA_W}
      height={KBAR_AREA_H}
      style={{ opacity: 0.6, display: 'block', maxWidth: '100%' }}
      aria-hidden="true"
    >
      {bars}
    </svg>
  );
}

// ──────────────────────────────────────────────
// Sparkline decoration (subtle background curve)
// ──────────────────────────────────────────────
function SparklineDecoration() {
  const pts = Array.from({ length: 40 }, (_, i) => {
    const y = 30 + pseudoRand(i * 31 + 100) * 40;
    return `${(i / 39) * 560},${y}`;
  }).join(' ');

  return (
    <svg
      width="560" height="80"
      style={{ opacity: 0.18, display: 'block', maxWidth: '100%', marginTop: 8 }}
      aria-hidden="true"
    >
      <polyline
        points={pts}
        fill="none"
        stroke="var(--dark-ink)"
        strokeWidth="1.25"
      />
    </svg>
  );
}

// ──────────────────────────────────────────────
// Main Login component
// ──────────────────────────────────────────────
export default function Login() {
  const navigate = useNavigate();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [rememberDevice, setRememberDevice] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Input focus tracking for border highlight
  const [emailFocused, setEmailFocused] = useState(false);
  const [passwordFocused, setPasswordFocused] = useState(false);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) {
      setError('請輸入 Email 和密碼');
      return;
    }
    setError('');
    setLoading(true);
    try {
      const res = await api.login(email, password);
      localStorage.setItem('access_token', res.access_token);
      if (rememberDevice) {
        localStorage.setItem('remember_device', '1');
      }
      navigate('/');
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '登入失敗，請確認帳號密碼';
      setError(msg || '登入失敗，請確認帳號密碼');
    } finally {
      setLoading(false);
    }
  }, [email, password, rememberDevice, navigate]);

  // Press Enter to submit
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSubmit(e as unknown as React.FormEvent);
    }
  }, [handleSubmit]);

  const inputStyle = (focused: boolean): React.CSSProperties => ({
    width: '100%',
    height: 38,
    padding: '0 10px',
    fontSize: 13,
    fontFamily: 'var(--font-ui)',
    color: 'var(--ink)',
    background: 'var(--panel)',
    border: `1px solid ${focused ? 'var(--ink)' : 'var(--hair-2)'}`,
    borderRadius: 2,
    outline: 'none',
    boxSizing: 'border-box',
  });

  return (
    <div style={{
      display: 'flex',
      height: '100vh',
      overflow: 'hidden',
      fontFamily: 'var(--font-ui)',
    }}>
      {/* ─────────────────────────────────────
          LEFT: Brand panel
      ───────────────────────────────────── */}
      <div style={{
        flex: 1,
        background: 'var(--ink-bg)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        position: 'relative',
      }}>
        {/* Grid texture overlay */}
        <div style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: 'linear-gradient(var(--dark-hair) 1px, transparent 1px), linear-gradient(90deg, var(--dark-hair) 1px, transparent 1px)',
          backgroundSize: '32px 32px',
          opacity: 0.06,
          pointerEvents: 'none',
        }} />

        {/* Header */}
        <header style={{
          padding: '24px 40px',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          borderBottom: '1px solid var(--dark-hair)',
          flexShrink: 0,
        }}>
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontWeight: 600,
            fontSize: 16,
            letterSpacing: '0.10em',
            color: 'var(--dark-ink)',
          }}>
            QUANT·AI
          </span>
          {/* Shioaji status indicator — mock gray */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 8 }}>
            <span style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: 'var(--dark-muted)',
            }} />
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              color: 'var(--dark-muted)',
              letterSpacing: '0.08em',
            }}>
              Shioaji
            </span>
          </div>
        </header>

        {/* Center Hero content */}
        <div style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: '0 40px',
          gap: 24,
        }}>
          {/* K-Bar decoration */}
          <div style={{ marginBottom: 8 }}>
            <KBarDecoration />
            <SparklineDecoration />
          </div>

          {/* Eyebrow */}
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            color: 'var(--dark-muted)',
            letterSpacing: '0.16em',
            textTransform: 'uppercase',
          }}>
            台股 · AI 量化交易工作站
          </div>

          {/* H1 hero title */}
          <h1 style={{
            margin: 0,
            fontSize: 56,
            fontWeight: 300,
            lineHeight: 1.1,
            color: 'var(--dark-ink)',
          }}>
            AI 驅動的
            <br />
            台股量化交易
            <br />
            每日&nbsp;<span style={{ color: 'var(--up)' }}>13:25</span>&nbsp;強制平倉
          </h1>

          {/* Sub-title */}
          <p style={{
            margin: 0,
            maxWidth: 480,
            fontSize: 14,
            lineHeight: 1.6,
            color: 'var(--dark-muted)',
          }}>
            結合 Claude AI 深度分析與 Shioaji API 即時下單，
            <br />
            在盤前完成選股、盤中自動監控、13:25 強制平倉。
          </p>
        </div>

        {/* Footer */}
        <div style={{
          padding: '10px 40px',
          borderTop: '1px solid var(--dark-hair)',
          fontFamily: 'var(--font-mono)',
          fontSize: 10,
          color: 'var(--dark-muted)',
          letterSpacing: '0.06em',
          flexShrink: 0,
        }}>
          © 2026 QUANT·AI · 模擬模式 · 非投資建議
        </div>
      </div>

      {/* ─────────────────────────────────────
          RIGHT: Form panel
      ───────────────────────────────────── */}
      <div style={{
        width: 560,
        flexShrink: 0,
        background: 'var(--surface)',
        borderLeft: '1px solid var(--hair)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'auto',
        padding: 56,
      }}>
        {/* Form header */}
        <div style={{ marginBottom: 32 }}>
          <h2 style={{
            margin: '0 0 6px',
            fontSize: 22,
            fontWeight: 600,
            color: 'var(--ink)',
          }}>
            登入工作站
          </h2>
          <p style={{
            margin: 0,
            fontSize: 12,
            color: 'var(--muted)',
          }}>
            交易日 08:30–13:35 即時運作
          </p>
        </div>

        {/* Form */}
        <form
          onSubmit={handleSubmit}
          onKeyDown={handleKeyDown}
          style={{ display: 'flex', flexDirection: 'column', gap: 16 }}
        >
          {/* Email field */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--ink-2)' }}>
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onFocus={() => setEmailFocused(true)}
              onBlur={() => setEmailFocused(false)}
              placeholder="you@example.com"
              autoComplete="email"
              style={inputStyle(emailFocused)}
            />
          </div>

          {/* Password field */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--ink-2)' }}>
              密碼
            </label>
            <div style={{ position: 'relative' }}>
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onFocus={() => setPasswordFocused(true)}
                onBlur={() => setPasswordFocused(false)}
                placeholder="••••••••"
                autoComplete="current-password"
                style={{
                  ...inputStyle(passwordFocused),
                  fontFamily: 'var(--font-mono)',
                  fontFeatureSettings: '"tnum" 1',
                  paddingRight: 40,
                }}
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                style={{
                  position: 'absolute',
                  right: 0,
                  top: 0,
                  height: 38,
                  width: 38,
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  color: 'var(--muted)',
                  fontSize: 12,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
                tabIndex={-1}
                aria-label={showPassword ? '隱藏密碼' : '顯示密碼'}
              >
                {showPassword ? '隱' : '示'}
              </button>
            </div>
          </div>

          {/* Forgot password */}
          <div style={{ textAlign: 'right' }}>
            <button
              type="button"
              style={{
                background: 'none',
                border: 'none',
                padding: 0,
                cursor: 'pointer',
                fontSize: 12,
                color: 'var(--muted)',
                textDecoration: 'underline',
              }}
            >
              忘記密碼
            </button>
          </div>

          {/* 2FA hint card */}
          <div style={{
            padding: '12px 14px',
            background: 'var(--gold-soft)',
            border: '1px solid var(--gold)',
            borderRadius: 2,
            fontSize: 12,
            color: 'var(--gold)',
            lineHeight: 1.6,
          }}>
            <span style={{ fontWeight: 500 }}>⚠ 兩步驟驗證</span>
            &nbsp;下一步將要求 6 位數 TOTP 驗證碼，請準備 Google Authenticator
          </div>

          {/* Remember device + SSL */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="checkbox"
              id="remember"
              checked={rememberDevice}
              onChange={(e) => setRememberDevice(e.target.checked)}
              style={{ width: 14, height: 14, cursor: 'pointer', flexShrink: 0 }}
            />
            <label
              htmlFor="remember"
              style={{ fontSize: 12, color: 'var(--ink-2)', cursor: 'pointer', flex: 1 }}
            >
              記住此裝置（30 天）
            </label>
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              color: 'var(--muted)',
              letterSpacing: '0.06em',
            }}>
              SSL 加密傳輸
            </span>
          </div>

          {/* Error message */}
          {error && (
            <div style={{
              padding: '10px 14px',
              background: 'var(--up-soft)',
              border: '1px solid var(--up)',
              borderRadius: 2,
              fontSize: 12,
              color: 'var(--up)',
              lineHeight: 1.5,
            }}>
              {error}
            </div>
          )}

          {/* Submit button + Enter hint */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Button
              type="submit"
              variant="primary"
              disabled={loading}
              style={{ flex: 1, height: 44, fontSize: 14 }}
            >
              {loading ? '驗證中…' : '繼續 · 兩步驟驗證'}
            </Button>
            <span style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              color: 'var(--muted)',
              whiteSpace: 'nowrap',
            }}>
              Enter ↵
            </span>
          </div>

          {/* Divider */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            margin: '4px 0',
          }}>
            <div style={{ flex: 1, height: 1, background: 'var(--hair)' }} />
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>或</span>
            <div style={{ flex: 1, height: 1, background: 'var(--hair)' }} />
          </div>

          {/* SSO buttons */}
          <div style={{ display: 'flex', gap: 10 }}>
            <Button
              type="button"
              variant="ghost"
              style={{
                flex: 1,
                height: 38,
                border: '1px solid var(--hair-2)',
                color: 'var(--ink-2)',
              }}
            >
              永豐金 API
            </Button>
            <Button
              type="button"
              variant="ghost"
              style={{
                flex: 1,
                height: 38,
                border: '1px solid var(--hair-2)',
                color: 'var(--ink-2)',
              }}
            >
              Google
            </Button>
          </div>
        </form>

        {/* Footer fineprint */}
        <div style={{
          marginTop: 'auto',
          paddingTop: 32,
          fontSize: 11,
          color: 'var(--muted-2)',
          lineHeight: 1.8,
          textAlign: 'center',
        }}>
          使用即代表同意服務條款
          <br />
          模擬模式不會動用真實資金
        </div>
      </div>
    </div>
  );
}
