// Shared component — Monthly returns heatmap
// Extracted from pages/Simulate.tsx (MonthlyHeatmap)

// ── Props ────────────────────────────────────────────────────────
export interface MonthlyHeatmapProps {
  returns: number[];
}

// ── Component ────────────────────────────────────────────────────
export default function MonthlyHeatmap({ returns }: MonthlyHeatmapProps) {
  const months = ['1月', '2月', '3月', '4月', '5月', '6月'];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 2 }}>
      {returns.map((r, i) => {
        const isPos = r >= 0;
        const intensity = Math.min(Math.abs(r) / 0.1, 1);
        const bg = isPos
          ? `rgba(200, 51, 43, ${0.15 + intensity * 0.6})`
          : `rgba(46, 125, 79, ${0.15 + intensity * 0.6})`;
        return (
          <div key={i} style={{ background: bg, borderRadius: 2, padding: '10px 6px', textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 4, fontFamily: 'var(--font-mono)' }}>{months[i]}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color: isPos ? 'var(--up)' : 'var(--down)' }}>
              {isPos ? '+' : ''}{(r * 100).toFixed(1)}%
            </div>
          </div>
        );
      })}
    </div>
  );
}
