interface ConfidenceTickProps {
  value: number;  // 0..1
}

const TICKS = 5;
const TICK_W = 4;
const TICK_GAP = 1;

function confidenceColor(value: number): string {
  if (value >= 0.75) return 'var(--up)';
  if (value >= 0.60) return 'var(--gold)';
  return 'var(--muted-2)';
}

export default function ConfidenceTick({ value }: ConfidenceTickProps) {
  const filled = Math.round(value * TICKS);
  const color = confidenceColor(value);

  return (
    <div
      style={{ display: 'inline-flex', gap: TICK_GAP, alignItems: 'flex-end' }}
      title={`${Math.round(value * 100)}%`}
    >
      {Array.from({ length: TICKS }).map((_, i) => (
        <div
          key={i}
          style={{
            width: TICK_W,
            height: 4 + i * 2,  // 4, 6, 8, 10, 12px — graduated heights
            background: i < filled ? color : 'var(--hair)',
            borderRadius: 0,
            flexShrink: 0,
          }}
        />
      ))}
    </div>
  );
}
