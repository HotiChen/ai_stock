import type { Contribution } from '../types';

interface ContribRowProps {
  contrib: Contribution;
}

const MAX_BAR = 100; // px, each side

export default function ContribRow({ contrib }: ContribRowProps) {
  const isPositive = contrib.delta >= 0;
  const pct = Math.min(Math.abs(contrib.delta), 1); // clamp 0..1
  const barWidth = pct * MAX_BAR;

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      padding: '5px 12px',
      borderBottom: '1px solid var(--hair)',
      fontSize: 11,
    }}>
      {/* Label */}
      <div style={{
        width: 130,
        flexShrink: 0,
        color: 'var(--ink-2)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>
        {contrib.key}
      </div>

      {/* Center-symmetric bar */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        width: MAX_BAR * 2 + 4,
        flexShrink: 0,
      }}>
        {/* Left (negative) side */}
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          width: MAX_BAR,
        }}>
          {!isPositive && (
            <div style={{
              width: barWidth,
              height: 8,
              background: 'var(--down)',
              borderRadius: '1px 0 0 1px',
            }} />
          )}
        </div>

        {/* Center line */}
        <div style={{
          width: 2,
          height: 14,
          background: 'var(--hair-2)',
          flexShrink: 0,
        }} />

        {/* Right (positive) side */}
        <div style={{ width: MAX_BAR }}>
          {isPositive && (
            <div style={{
              width: barWidth,
              height: 8,
              background: 'var(--up)',
              borderRadius: '0 1px 1px 0',
            }} />
          )}
        </div>
      </div>

      {/* Detail */}
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1, "zero" 1',
        fontSize: 11,
        color: isPositive ? 'var(--up)' : 'var(--down)',
        flexShrink: 0,
        minWidth: 50,
      }}>
        {isPositive ? '+' : ''}{(contrib.delta * 100).toFixed(1)}%
      </div>

      {/* Sub detail */}
      <div style={{
        fontSize: 10,
        color: 'var(--muted)',
        flex: 1,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>
        {contrib.detail}
      </div>
    </div>
  );
}
