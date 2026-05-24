// 06 Settings — Wave 2-E 完整實作
import { useState, useEffect, useCallback } from 'react';
import AppChrome from '../components/AppChrome';
import Button from '../components/Button';
import type { AppSettings, AlertLevel } from '../types';
import { api } from '../api';

// ──────────────────────────────────────────────
// Left Sub-nav
// ──────────────────────────────────────────────
const SUB_NAV_ITEMS = [
  { id: 'capital',      label: '資金與風控' },
  { id: 'api-keys',     label: 'API 金鑰' },
  { id: 'ai-models',    label: 'AI 模型' },
  { id: 'mode',         label: '模擬 · 真實' },
  { id: 'notify',       label: '通知偏好' },
  { id: 'blacklist',    label: '黑名單' },
  { id: 'theme',        label: '主題' },
  { id: 'export',       label: '匯出' },
  { id: 'about',        label: '關於' },
];

// ──────────────────────────────────────────────
// Stepper component
// ──────────────────────────────────────────────
interface StepperProps {
  label: string;
  value: number;
  step: number;
  min?: number;
  max?: number;
  prefix?: string;
  suffix?: string;
  onChange: (v: number) => void;
  formatter?: (v: number) => string;
}

function Stepper({ label, value, step, min = 0, max = Infinity, prefix, suffix, onChange, formatter }: StepperProps) {
  const display = formatter ? formatter(value) : value.toLocaleString();
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '10px 0',
      borderBottom: '1px solid var(--hair)',
    }}>
      <span style={{ fontSize: 13, color: 'var(--ink-2)', flex: 1 }}>{label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexShrink: 0 }}>
        <button
          type="button"
          onClick={() => onChange(Math.max(min, value - step))}
          style={{
            width: 28,
            height: 28,
            border: '1px solid var(--hair-2)',
            borderRight: 'none',
            background: 'var(--surface)',
            cursor: 'pointer',
            fontSize: 16,
            color: 'var(--ink)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: '2px 0 0 2px',
          }}
        >−</button>
        <div style={{
          height: 28,
          minWidth: 120,
          border: '1px solid var(--hair-2)',
          background: 'var(--panel)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: 'var(--font-mono)',
          fontFeatureSettings: '"tnum" 1',
          fontSize: 12,
          color: 'var(--ink)',
          padding: '0 8px',
          whiteSpace: 'nowrap',
        }}>
          {prefix && <span style={{ color: 'var(--muted)', marginRight: 4 }}>{prefix}</span>}
          {display}
          {suffix && <span style={{ color: 'var(--muted)', marginLeft: 4 }}>{suffix}</span>}
        </div>
        <button
          type="button"
          onClick={() => onChange(Math.min(max, value + step))}
          style={{
            width: 28,
            height: 28,
            border: '1px solid var(--hair-2)',
            borderLeft: 'none',
            background: 'var(--surface)',
            cursor: 'pointer',
            fontSize: 16,
            color: 'var(--ink)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: '0 2px 2px 0',
          }}
        >+</button>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────
// Slider component
// ──────────────────────────────────────────────
interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format?: (v: number) => string;
  onChange: (v: number) => void;
}

function SliderRow({ label, value, min, max, step, format, onChange }: SliderProps) {
  const pct = ((value - min) / (max - min)) * 100;
  const display = format ? format(value) : String(value);

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '160px 1fr 80px',
      alignItems: 'center',
      gap: 16,
      padding: '10px 0',
      borderBottom: '1px solid var(--hair)',
    }}>
      <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{label}</span>
      <div style={{ position: 'relative', height: 20, display: 'flex', alignItems: 'center' }}>
        {/* Track background */}
        <div style={{
          position: 'absolute',
          left: 0,
          right: 0,
          height: 3,
          background: 'var(--hair)',
          borderRadius: 2,
        }} />
        {/* Track fill */}
        <div style={{
          position: 'absolute',
          left: 0,
          width: `${pct}%`,
          height: 3,
          background: 'var(--ink)',
          borderRadius: 2,
        }} />
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            width: '100%',
            opacity: 0,
            cursor: 'pointer',
            height: 20,
            zIndex: 1,
          }}
        />
        {/* Thumb visual */}
        <div style={{
          position: 'absolute',
          left: `calc(${pct}% - 6px)`,
          width: 12,
          height: 12,
          borderRadius: '50%',
          background: 'var(--ink)',
          border: '2px solid var(--surface)',
          boxShadow: '0 0 0 1px var(--hair-2)',
          pointerEvents: 'none',
        }} />
      </div>
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1',
        fontSize: 12,
        color: 'var(--ink)',
        textAlign: 'right',
      }}>
        {display}
      </span>
    </div>
  );
}

// ──────────────────────────────────────────────
// Section header
// ──────────────────────────────────────────────
function SectionHeader({ title }: { title: string }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      marginBottom: 4,
      marginTop: 24,
    }}>
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 10,
        letterSpacing: '0.16em',
        textTransform: 'uppercase',
        color: 'var(--muted)',
        whiteSpace: 'nowrap',
      }}>
        {title}
      </span>
      <div style={{ flex: 1, height: 1, background: 'var(--hair)' }} />
    </div>
  );
}

// ──────────────────────────────────────────────
// Toggle switch
// ──────────────────────────────────────────────
function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      style={{
        width: 32,
        height: 18,
        borderRadius: 20,
        background: checked ? 'var(--ink)' : 'var(--hair-2)',
        position: 'relative',
        cursor: 'pointer',
        flexShrink: 0,
        transition: 'background 0.15s',
      }}
    >
      <div style={{
        position: 'absolute',
        top: 2,
        left: checked ? 16 : 2,
        width: 14,
        height: 14,
        borderRadius: '50%',
        background: '#fff',
        transition: 'left 0.15s',
      }} />
    </div>
  );
}

// ──────────────────────────────────────────────
// Notification channel row
// ──────────────────────────────────────────────
interface NotifyChannel {
  key: string;
  label: string;
  desc: string;
  enabled: boolean;
  levels: AlertLevel[];
}

interface NotifyRowProps {
  channel: NotifyChannel;
  onToggleEnabled: () => void;
  onToggleLevel: (level: AlertLevel) => void;
}

function NotifyRow({ channel, onToggleEnabled, onToggleLevel }: NotifyRowProps) {
  const LEVELS: AlertLevel[] = ['high', 'med', 'low'];
  const LEVEL_LABELS: Record<AlertLevel, string> = { high: '高', med: '中', low: '低' };

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      padding: '12px 0',
      borderBottom: '1px solid var(--hair)',
      gap: 16,
    }}>
      {/* Left: name + desc */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink)' }}>{channel.label}</div>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{channel.desc}</div>
      </div>

      {/* Right: toggle + level checkboxes */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
        {/* Level checkboxes */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {LEVELS.map((lvl) => (
            <label
              key={lvl}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                cursor: 'pointer',
                fontSize: 11,
                color: 'var(--muted)',
              }}
            >
              <input
                type="checkbox"
                checked={channel.levels.includes(lvl)}
                onChange={() => onToggleLevel(lvl)}
                style={{ width: 12, height: 12, cursor: 'pointer' }}
              />
              {LEVEL_LABELS[lvl]}
            </label>
          ))}
        </div>
        {/* Enable toggle */}
        <Toggle checked={channel.enabled} onChange={onToggleEnabled} />
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────
// Mode switch confirmation modal
// ──────────────────────────────────────────────
interface ModeModalProps {
  userEmail: string;
  onConfirm: () => void;
  onCancel: () => void;
}

function ModeModal({ userEmail, onConfirm, onCancel }: ModeModalProps) {
  const [input, setInput] = useState('');
  const match = input.trim() === userEmail;

  return (
    <div style={{
      position: 'fixed',
      inset: 0,
      background: 'rgba(0,0,0,0.5)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000,
    }}>
      <div style={{
        background: 'var(--surface)',
        border: '2px solid var(--up)',
        borderRadius: 2,
        padding: 32,
        maxWidth: 480,
        width: '90%',
      }}>
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 10,
          letterSpacing: '0.16em',
          textTransform: 'uppercase',
          color: 'var(--up)',
          marginBottom: 12,
        }}>
          危險操作 · 真實交易模式
        </div>
        <h3 style={{ margin: '0 0 12px', fontSize: 16, fontWeight: 600, color: 'var(--ink)' }}>
          切換為真實交易模式
        </h3>
        <p style={{ margin: '0 0 20px', fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.6 }}>
          此操作將使用真實資金透過 Shioaji API 下單。請輸入您的 Email 完整地址以確認此操作。
        </p>
        <div style={{
          padding: '10px 14px',
          background: 'var(--up-soft)',
          border: '1px solid var(--up)',
          borderRadius: 2,
          fontSize: 12,
          color: 'var(--up)',
          marginBottom: 20,
          lineHeight: 1.5,
        }}>
          ⚠ 真實交易模式下，所有委託將直接透過永豐金 API 送出，請謹慎操作。
        </div>
        <label style={{ display: 'block', fontSize: 12, fontWeight: 500, color: 'var(--ink-2)', marginBottom: 8 }}>
          輸入您的 Email：{userEmail}
        </label>
        <input
          type="email"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={userEmail}
          autoFocus
          style={{
            width: '100%',
            height: 38,
            padding: '0 10px',
            fontSize: 13,
            fontFamily: 'var(--font-mono)',
            color: 'var(--ink)',
            background: 'var(--panel)',
            border: `1px solid ${match ? 'var(--down)' : 'var(--hair-2)'}`,
            borderRadius: 2,
            outline: 'none',
            boxSizing: 'border-box',
            marginBottom: 20,
          }}
        />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <Button type="button" variant="default" onClick={onCancel}>取消</Button>
          <Button
            type="button"
            variant="danger"
            disabled={!match}
            onClick={onConfirm}
          >
            確認切換為真實交易
          </Button>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────
// Capital & Risk section (main content)
// ──────────────────────────────────────────────
interface CapitalPanelProps {
  settings: AppSettings;
  onUpdate: (patch: Partial<AppSettings>) => void;
}

function CapitalPanel({ settings, onUpdate }: CapitalPanelProps) {
  const [showModeModal, setShowModeModal] = useState(false);

  // Notification channels derived from settings
  type NotifyKey = 'telegram' | 'email' | 'desktop' | 'ios_push' | 'slack';

  const CHANNEL_DEFS: { key: NotifyKey; label: string; desc: string }[] = [
    { key: 'telegram',  label: 'Telegram',     desc: '透過 Bot 推送警報與下單確認' },
    { key: 'email',     label: 'Email',        desc: '每日收盤後發送日報摘要' },
    { key: 'desktop',   label: 'Desktop',      desc: '瀏覽器推播通知（需授權）' },
    { key: 'ios_push',  label: 'iOS Push',     desc: 'iPhone 推播（需安裝 PWA）' },
    { key: 'slack',     label: 'Slack',        desc: '透過 Webhook 推送到頻道' },
  ];

  const channels: NotifyChannel[] = CHANNEL_DEFS.map(({ key, label, desc }) => ({
    key,
    label,
    desc,
    enabled: settings.notify[key].enabled,
    levels: settings.notify[key].levels,
  }));

  function toggleChannelEnabled(key: NotifyKey) {
    onUpdate({
      notify: {
        ...settings.notify,
        [key]: { ...settings.notify[key], enabled: !settings.notify[key].enabled },
      },
    });
  }

  function toggleChannelLevel(key: NotifyKey, level: AlertLevel) {
    const cur = settings.notify[key].levels;
    const next = cur.includes(level) ? cur.filter((l) => l !== level) : [...cur, level];
    onUpdate({
      notify: {
        ...settings.notify,
        [key]: { ...settings.notify[key], levels: next },
      },
    });
  }

  // .env preview generation
  const envPreview = [
    `BUDGET=${settings.budget}`,
    `ORDER_HARD_LIMIT=${settings.order_hard_limit}`,
    `DAILY_MAX_LOSS_PCT=${settings.daily_max_loss_pct}`,
    `MAX_POSITION_RATIO=${settings.max_position_ratio}`,
    `MAX_SECTOR_RATIO=${settings.max_sector_ratio}`,
    `ENTRY_CONFIDENCE_THRESHOLD=${settings.entry_confidence_threshold}`,
    `DEFAULT_STOP_LOSS_PCT=${settings.default_stop_loss_pct}`,
    `MODEL_PREMARKET=${settings.model_premarket}`,
    `MODEL_DASHBOARD=${settings.model_dashboard}`,
    `MODEL_CHAT=${settings.model_chat}`,
    `APP_MODE=${settings.mode}`,
    `ANTHROPIC_API_KEY=sk-ant-***masked***`,
    `SHIOAJI_API_KEY=***masked***`,
    `TELEGRAM_TOKEN=***masked***`,
    `MONITOR_POLL_SECONDS=${settings.monitor_poll_seconds}`,
    `DB_PATH=${settings.db_path}`,
  ].join('\n');

  const MODE_USER_EMAIL = 'hotichen@gmail.com'; // placeholder — ideally from auth context

  return (
    <>
      {/* ── Section 1: Capital ───────────────────── */}
      <SectionHeader title="Capital · 資本配置" />

      <Stepper
        label="總預算（BUDGET）"
        value={settings.budget}
        step={10000}
        min={0}
        prefix="NT$"
        onChange={(v) => onUpdate({ budget: v })}
        formatter={(v) => v.toLocaleString()}
      />
      <Stepper
        label="單筆委託上限"
        value={settings.order_hard_limit}
        step={1000}
        min={0}
        prefix="NT$"
        onChange={(v) => onUpdate({ order_hard_limit: v })}
        formatter={(v) => v.toLocaleString()}
      />
      <Stepper
        label="日內最大虧損（%）"
        value={settings.daily_max_loss_pct * 100}
        step={0.5}
        min={0}
        max={100}
        suffix="%"
        onChange={(v) => onUpdate({ daily_max_loss_pct: v / 100 })}
        formatter={(v) => v.toFixed(1)}
      />

      {/* ── Section 2: Risk parameters ──────────── */}
      <SectionHeader title="風控參數" />

      <SliderRow
        label="單一部位上限"
        value={settings.max_position_ratio}
        min={0}
        max={1}
        step={0.01}
        format={(v) => `${(v * 100).toFixed(0)}%`}
        onChange={(v) => onUpdate({ max_position_ratio: v })}
      />
      <SliderRow
        label="板塊集中度上限"
        value={settings.max_sector_ratio}
        min={0}
        max={1}
        step={0.01}
        format={(v) => `${(v * 100).toFixed(0)}%`}
        onChange={(v) => onUpdate({ max_sector_ratio: v })}
      />
      <SliderRow
        label="入場信心門檻"
        value={settings.entry_confidence_threshold}
        min={0}
        max={1}
        step={0.01}
        format={(v) => v.toFixed(2)}
        onChange={(v) => onUpdate({ entry_confidence_threshold: v })}
      />
      <SliderRow
        label="預設停損幅度"
        value={settings.default_stop_loss_pct}
        min={0}
        max={0.2}
        step={0.005}
        format={(v) => `${(v * 100).toFixed(1)}%`}
        onChange={(v) => onUpdate({ default_stop_loss_pct: v })}
      />

      {/* ── Section 3: AI model allocation ──────── */}
      <SectionHeader title="AI 模型分配" />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 8 }}>
        {(
          [
            { label: '盤前選股', key: 'model_premarket' as const },
            { label: '策略監控', key: 'model_dashboard' as const },
            { label: 'AI 顧問',  key: 'model_chat' as const },
          ] as { label: string; key: 'model_premarket' | 'model_dashboard' | 'model_chat' }[]
        ).map(({ label, key }) => (
          <div key={key} style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '10px 0',
            borderBottom: '1px solid var(--hair)',
          }}>
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{label}</span>
            <select
              value={settings[key]}
              onChange={(e) => onUpdate({ [key]: e.target.value })}
              style={{
                height: 30,
                padding: '0 8px',
                fontSize: 12,
                fontFamily: 'var(--font-mono)',
                color: 'var(--ink)',
                background: 'var(--panel)',
                border: '1px solid var(--hair-2)',
                borderRadius: 2,
                cursor: 'pointer',
                minWidth: 260,
              }}
            >
              <option value="claude-haiku-4-5-20251001">claude-haiku-4-5-20251001</option>
              <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
              <option value="claude-opus-4-5">claude-opus-4-5</option>
            </select>
          </div>
        ))}
      </div>

      {/* ── Section 4: Execution mode ────────────── */}
      <SectionHeader title="執行模式" />

      <div style={{ display: 'flex', gap: 16, marginBottom: 8 }}>
        {/* Simulation card */}
        <div style={{
          flex: 1,
          border: settings.mode === 'simulation' ? '2px solid var(--gold)' : '1px solid var(--hair)',
          borderRadius: 2,
          padding: 16,
          position: 'relative',
          background: settings.mode === 'simulation' ? 'var(--gold-soft)' : 'var(--surface)',
        }}>
          {settings.mode === 'simulation' && (
            <div style={{
              position: 'absolute',
              top: 8,
              right: 8,
              fontFamily: 'var(--font-mono)',
              fontSize: 9,
              letterSpacing: '0.10em',
              padding: '2px 6px',
              background: 'var(--gold)',
              color: '#fff',
              borderRadius: 2,
            }}>
              CURRENT
            </div>
          )}
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 6 }}>
            模擬模式
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }}>
            不動用真實資金，使用 Shioaji 模擬帳戶
          </div>
        </div>

        {/* Live trading card */}
        <div style={{
          flex: 1,
          border: settings.mode === 'live' ? '2px solid var(--up)' : '1px solid var(--hair)',
          borderRadius: 2,
          padding: 16,
          position: 'relative',
          background: settings.mode === 'live' ? 'var(--up-soft)' : 'var(--surface)',
        }}>
          {settings.mode === 'live' && (
            <div style={{
              position: 'absolute',
              top: 8,
              right: 8,
              fontFamily: 'var(--font-mono)',
              fontSize: 9,
              letterSpacing: '0.10em',
              padding: '2px 6px',
              background: 'var(--up)',
              color: '#fff',
              borderRadius: 2,
            }}>
              LIVE
            </div>
          )}
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', marginBottom: 6 }}>
            真實交易
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6, marginBottom: 12 }}>
            ⚠ 將使用真實資金下單
          </div>
          {settings.mode !== 'live' && (
            <Button
              type="button"
              variant="danger"
              size="small"
              onClick={() => setShowModeModal(true)}
            >
              切換為真實
            </Button>
          )}
          {settings.mode === 'live' && (
            <Button
              type="button"
              variant="default"
              size="small"
              onClick={() => onUpdate({ mode: 'simulation' })}
            >
              切換回模擬
            </Button>
          )}
        </div>
      </div>

      {/* ── Section 5: Notification preferences ── */}
      <SectionHeader title="通知偏好" />

      {channels.map((ch) => (
        <NotifyRow
          key={ch.key}
          channel={ch}
          onToggleEnabled={() => toggleChannelEnabled(ch.key as NotifyKey)}
          onToggleLevel={(lvl) => toggleChannelLevel(ch.key as NotifyKey, lvl)}
        />
      ))}

      {/* ── .env preview ─────────────────────────── */}
      <SectionHeader title=".env preview · 目前設定" />

      <div style={{
        background: 'var(--ink-bg)',
        border: '1px solid var(--dark-hair)',
        borderRadius: 2,
        padding: 16,
        marginTop: 4,
        marginBottom: 24,
        overflow: 'auto',
      }}>
        <pre style={{
          margin: 0,
          fontFamily: 'var(--font-mono)',
          fontFeatureSettings: '"tnum" 1',
          fontSize: 11,
          lineHeight: 1.7,
          color: 'var(--dark-ink)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
        }}>
          {envPreview}
        </pre>
      </div>

      {/* Mode confirmation modal */}
      {showModeModal && (
        <ModeModal
          userEmail={MODE_USER_EMAIL}
          onConfirm={() => {
            onUpdate({ mode: 'live' });
            setShowModeModal(false);
          }}
          onCancel={() => setShowModeModal(false)}
        />
      )}
    </>
  );
}

// ──────────────────────────────────────────────
// TODO panel for unimplemented sections
// ──────────────────────────────────────────────
function TodoPanel({ label }: { label: string }) {
  return (
    <div style={{
      padding: '48px 0',
      textAlign: 'center',
      color: 'var(--muted)',
      fontSize: 13,
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 10,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
        marginBottom: 8,
      }}>
        TODO
      </div>
      {label} 分頁 — 待實作
    </div>
  );
}

// ──────────────────────────────────────────────
// Skeleton loader
// ──────────────────────────────────────────────
function Skeleton() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '24px 0' }}>
      {[180, 160, 140, 180, 160].map((w, i) => (
        <div
          key={i}
          style={{
            height: 14,
            width: w,
            background: 'var(--hair)',
            borderRadius: 2,
            opacity: 0.6,
          }}
        />
      ))}
    </div>
  );
}

// ──────────────────────────────────────────────
// Default settings fallback
// ──────────────────────────────────────────────
const DEFAULT_SETTINGS: AppSettings = {
  budget: 500000,
  order_hard_limit: 50000,
  daily_max_loss_pct: 0.03,
  max_position_ratio: 0.05,
  max_sector_ratio: 0.30,
  entry_confidence_threshold: 0.60,
  default_stop_loss_pct: 0.03,
  model_premarket: 'claude-haiku-4-5-20251001',
  model_dashboard: 'claude-haiku-4-5-20251001',
  model_chat: 'claude-sonnet-4-6',
  mode: 'simulation',
  notify: {
    telegram: { enabled: true,  chat_id: '', levels: ['high', 'med'] },
    email:    { enabled: false, address: '', levels: ['high'] },
    desktop:  { enabled: false, levels: ['high'] },
    ios_push: { enabled: false, device_token: '', levels: ['high', 'med'] },
    slack:    { enabled: false, webhook: '', levels: ['high'] },
  },
  blacklist: [],
  theme: 'light',
  language: 'zh-TW',
  api_keys: {
    anthropic: '***',
    shioaji:   '***',
    telegram:  '***',
  },
  monitor_poll_seconds: 30,
  db_path: './data/research.db',
  version: '0.1.0',
};

// ──────────────────────────────────────────────
// Main Settings component
// ──────────────────────────────────────────────
export default function Settings() {
  const [activeSection, setActiveSection] = useState('capital');
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  // Load settings on mount
  useEffect(() => {
    let cancelled = false;
    api.getSettings()
      .then((s) => { if (!cancelled) { setSettings(s); setLoading(false); } })
      .catch(() => {
        // Fall back to defaults on API error (during development)
        if (!cancelled) { setSettings(DEFAULT_SETTINGS); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, []);

  // Merge patch into local state
  const handleUpdate = useCallback((patch: Partial<AppSettings>) => {
    setSettings((prev) => prev ? { ...prev, ...patch } : prev);
  }, []);

  // Save to API
  const handleSave = useCallback(async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const updated = await api.patchSettings(settings);
      setSettings(updated);
      setSaveMsg('已儲存 · 08:30 生效');
      setTimeout(() => setSaveMsg(''), 3000);
    } catch {
      setSaveMsg('儲存失敗，請重試');
      setTimeout(() => setSaveMsg(''), 3000);
    } finally {
      setSaving(false);
    }
  }, [settings]);

  return (
    <AppChrome title="設定" eyebrow="06">
      <div style={{
        display: 'flex',
        height: '100%',
        overflow: 'hidden',
      }}>
        {/* ── Left sub-nav (220px) ────────────── */}
        <nav style={{
          width: 220,
          flexShrink: 0,
          borderRight: '1px solid var(--hair)',
          background: 'var(--surface)',
          display: 'flex',
          flexDirection: 'column',
          overflowY: 'auto',
          paddingTop: 8,
        }}>
          {SUB_NAV_ITEMS.map((item) => {
            const active = activeSection === item.id;
            return (
              <div
                key={item.id}
                role="button"
                tabIndex={0}
                onClick={() => setActiveSection(item.id)}
                onKeyDown={(e) => e.key === 'Enter' && setActiveSection(item.id)}
                style={{
                  height: 38,
                  padding: '0 14px',
                  display: 'flex',
                  alignItems: 'center',
                  cursor: 'pointer',
                  fontSize: 13,
                  color: active ? 'var(--ink)' : 'var(--ink-2)',
                  background: active ? 'var(--surface-2)' : 'transparent',
                  borderLeft: active ? '2px solid var(--ink)' : '2px solid transparent',
                  userSelect: 'none',
                }}
              >
                {item.label}
              </div>
            );
          })}
        </nav>

        {/* ── Right main body ─────────────────── */}
        <div style={{
          flex: 1,
          overflow: 'auto',
          background: 'var(--bg)',
        }}>
          <div style={{
            maxWidth: 800,
            padding: '24px 32px 48px',
          }}>
            {/* Section title */}
            <h2 style={{
              margin: '0 0 4px',
              fontSize: 18,
              fontWeight: 600,
              color: 'var(--ink)',
            }}>
              {SUB_NAV_ITEMS.find((i) => i.id === activeSection)?.label}
            </h2>
            <p style={{ margin: '0 0 8px', fontSize: 12, color: 'var(--muted)' }}>
              {activeSection === 'capital' && '資金上限、部位風控與停損參數'}
            </p>

            {/* Content */}
            {loading ? (
              <Skeleton />
            ) : settings ? (
              <>
                {activeSection === 'capital' && (
                  <CapitalPanel settings={settings} onUpdate={handleUpdate} />
                )}
                {activeSection !== 'capital' && (
                  <TodoPanel label={SUB_NAV_ITEMS.find((i) => i.id === activeSection)?.label ?? ''} />
                )}

                {/* Save bar — only for capital section */}
                {activeSection === 'capital' && (
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 16,
                    paddingTop: 16,
                    borderTop: '1px solid var(--hair)',
                  }}>
                    <Button
                      type="button"
                      variant="primary"
                      disabled={saving}
                      onClick={handleSave}
                      style={{ height: 38 }}
                    >
                      {saving ? '儲存中…' : '儲存設定'}
                    </Button>
                    {saveMsg ? (
                      <span style={{
                        fontSize: 12,
                        color: saveMsg.includes('失敗') ? 'var(--up)' : 'var(--down)',
                        fontFamily: 'var(--font-mono)',
                        fontFeatureSettings: '"tnum" 1',
                      }}>
                        {saveMsg}
                      </span>
                    ) : (
                      <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                        將在下一個 PremarketJob（08:30）生效
                      </span>
                    )}
                  </div>
                )}
              </>
            ) : null}
          </div>
        </div>
      </div>
    </AppChrome>
  );
}
