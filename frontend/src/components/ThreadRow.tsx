import type { StrategyThread } from '../types';
import Pill from './Pill';

interface ThreadRowProps {
  thread: StrategyThread;
}

const stateColor: Record<string, string> = {
  pending:       'var(--muted)',
  monitoring:    'var(--gold)',
  closed_tp:     'var(--down)',
  closed_sl:     'var(--up)',
  closed_force:  'var(--up)',
  rejected:      'var(--muted-2)',
};

const stateBg: Record<string, string> = {
  pending:       'transparent',
  monitoring:    'var(--gold-soft)',
  closed_tp:     'var(--down-soft)',
  closed_sl:     'var(--up-soft)',
  closed_force:  'var(--up-soft)',
  rejected:      'transparent',
};

const stateLabel: Record<string, string> = {
  pending:       '待命',
  monitoring:    '監控中',
  closed_tp:     '止盈',
  closed_sl:     '停損',
  closed_force:  '強平',
  rejected:      '拒絕',
};

export default function ThreadRow({ thread }: ThreadRowProps) {
  const color = stateColor[thread.state] ?? 'var(--muted)';
  const bg = stateBg[thread.state] ?? 'transparent';
  const label = stateLabel[thread.state] ?? thread.state;

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      padding: '8px 12px',
      borderBottom: '1px solid var(--hair)',
      minHeight: 38,
      background: 'var(--surface)',
    }}>
      {/* Code + Name */}
      <div style={{ display: 'flex', flexDirection: 'column', minWidth: 80 }}>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontWeight: 600,
          fontSize: 12,
          color: 'var(--ink)',
        }}>
          {thread.code}
        </span>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>
          {thread.name}
        </span>
      </div>

      {/* State pill */}
      <Pill color={color} bg={bg} border={color} size={10}>
        {label}
      </Pill>

      {/* Distance label */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 11,
        color: 'var(--ink-2)',
        flex: 1,
      }}>
        {thread.distance_label}
      </span>

      {/* Poll count */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1, "zero" 1',
        fontSize: 10,
        color: 'var(--muted)',
        flexShrink: 0,
      }}>
        {thread.poll_count}次
      </span>

      {/* Age */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1, "zero" 1',
        fontSize: 10,
        color: 'var(--muted)',
        flexShrink: 0,
        minWidth: 40,
        textAlign: 'right',
      }}>
        {Math.floor(thread.age_seconds / 60)}m
      </span>
    </div>
  );
}
