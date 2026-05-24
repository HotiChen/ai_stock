interface ConfidenceBarProps {
  value: number;    // 0..1
  label?: boolean;  // 顯示數字
}

const SEGMENTS = 10;
const SEG_W = 8;
const SEG_GAP = 2;

function confidenceColor(value: number): string {
  if (value >= 0.75) return 'var(--up)';
  if (value >= 0.60) return 'var(--gold)';
  return 'var(--muted-2)';
}

export default function ConfidenceBar({ value, label = false }: ConfidenceBarProps) {
  const filled = Math.round(value * SEGMENTS);
  const color = confidenceColor(value);
  const totalWidth = SEGMENTS * SEG_W + (SEGMENTS - 1) * SEG_GAP;

  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <div style={{ display: 'flex', gap: SEG_GAP, alignItems: 'center' }}>
        {Array.from({ length: SEGMENTS }).map((_, i) => (
          <div
            key={i}
            style={{
              width: SEG_W,
              height: 6,
              background: i < filled ? color : 'var(--hair)',
              borderRadius: 1,
              flexShrink: 0,
            }}
          />
        ))}
      </div>
      {label && (
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontFeatureSettings: '"tnum" 1, "zero" 1',
          fontSize: 10,
          color,
          minWidth: totalWidth > 0 ? undefined : 28,
        }}>
          {Math.round(value * 100)}%
        </span>
      )}
    </div>
  );
}
