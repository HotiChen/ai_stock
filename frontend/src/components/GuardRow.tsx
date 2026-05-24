import type { RiskCheck } from '../types';

interface GuardRowProps {
  check: RiskCheck;
  index: number;
}

const statusIcon: Record<string, string> = {
  pass: '✓',
  warn: '!',
  fail: '✗',
};

const statusColor: Record<string, string> = {
  pass: 'var(--down)',
  warn: 'var(--gold)',
  fail: 'var(--up)',
};

const statusBg: Record<string, string> = {
  pass: 'var(--down-soft)',
  warn: 'var(--gold-soft)',
  fail: 'var(--up-soft)',
};

export default function GuardRow({ check, index }: GuardRowProps) {
  const color = statusColor[check.status] ?? 'var(--muted)';
  const bg = statusBg[check.status] ?? 'transparent';
  const icon = statusIcon[check.status] ?? '?';

  return (
    <div style={{
      display: 'flex',
      alignItems: 'flex-start',
      gap: 10,
      padding: '8px 12px',
      borderBottom: '1px solid var(--hair)',
      background: check.status === 'fail' ? 'var(--up-soft)' : 'var(--surface)',
    }}>
      {/* Index */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1, "zero" 1',
        fontSize: 10,
        color: 'var(--muted-2)',
        width: 16,
        flexShrink: 0,
        paddingTop: 1,
      }}>
        {String(index + 1).padStart(2, '0')}
      </span>

      {/* Status badge */}
      <div style={{
        width: 18,
        height: 18,
        borderRadius: 2,
        background: bg,
        border: `1px solid ${color}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 10,
        fontWeight: 600,
        color,
        flexShrink: 0,
      }}>
        {icon}
      </div>

      {/* Key + sub */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 12,
          fontWeight: 500,
          color: 'var(--ink)',
          marginBottom: 1,
        }}>
          {check.key}
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 10,
          color: 'var(--muted)',
        }}>
          {check.sub}
        </div>
      </div>

      {/* Detail */}
      <div style={{
        fontSize: 11,
        color: check.status === 'fail' ? 'var(--up)' : 'var(--muted)',
        flexShrink: 0,
        maxWidth: 160,
        textAlign: 'right',
      }}>
        {check.detail}
      </div>
    </div>
  );
}
