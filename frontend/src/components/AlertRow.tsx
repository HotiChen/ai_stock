import type { Alert } from '../types';
import Pill from './Pill';

interface AlertRowProps {
  alert: Alert;
  onClick?: () => void;
}

const levelColor: Record<string, string> = {
  high: 'var(--up)',
  med:  'var(--gold)',
  low:  'var(--muted)',
};

const levelBg: Record<string, string> = {
  high: 'var(--up-soft)',
  med:  'var(--gold-soft)',
  low:  'transparent',
};

export default function AlertRow({ alert, onClick }: AlertRowProps) {
  const borderColor = levelColor[alert.level] ?? 'var(--muted)';

  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 12px',
        borderLeft: `3px solid ${borderColor}`,
        background: alert.resolved ? 'transparent' : levelBg[alert.level],
        cursor: onClick ? 'pointer' : 'default',
        opacity: alert.resolved ? 0.5 : 1,
        borderBottom: '1px solid var(--hair)',
        minHeight: 38,
      }}
    >
      {/* Time */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1, "zero" 1',
        fontSize: 10,
        color: 'var(--muted)',
        flexShrink: 0,
        width: 54,
      }}>
        {alert.time}
      </span>

      {/* Level pill */}
      <Pill
        color={levelColor[alert.level]}
        bg={levelBg[alert.level]}
        border={levelColor[alert.level]}
        size={10}
      >
        {alert.level.toUpperCase()}
      </Pill>

      {/* Code + name */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontWeight: 600,
        fontSize: 11,
        color: 'var(--ink)',
        flexShrink: 0,
      }}>
        {alert.code}
      </span>
      <span style={{
        fontSize: 11,
        color: 'var(--ink-2)',
        flexShrink: 0,
      }}>
        {alert.name}
      </span>

      {/* Text */}
      <span style={{
        fontSize: 11,
        color: 'var(--muted)',
        flex: 1,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>
        {alert.text}
      </span>

      {/* Telegram sent indicator */}
      {alert.telegram_sent && (
        <span style={{
          fontSize: 10,
          color: 'var(--muted-2)',
          flexShrink: 0,
        }}>
          TG
        </span>
      )}
    </div>
  );
}
