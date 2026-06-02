interface DistRangeBarProps {
  slPct: number;           // 距 SL 的 % (負數, e.g. -3.3)
  tpPct: number;           // 距 TP 的 % (正數, e.g. +4.4)
  currentIsSafe: boolean;
}

export default function DistRangeBar({ slPct, tpPct, currentIsSafe }: DistRangeBarProps) {
  // Normalize: sl is negative, tp is positive
  const sl = Math.abs(slPct);
  const tp = Math.abs(tpPct);
  const total = sl + tp || 1;

  const slWidth = (sl / total) * 100;
  const tpWidth = (tp / total) * 100;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {/* Label row */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        fontFamily: 'var(--font-mono)',
        fontSize: 10,
        color: 'var(--muted)',
      }}>
        <span className="down">SL {slPct > 0 ? '+' : ''}{slPct.toFixed(1)}%</span>
        <span className="up">TP +{tpPct.toFixed(1)}%</span>
      </div>

      {/* Bar */}
      <div style={{ display: 'flex', height: 6, width: '100%' }}>
        {/* SL side (left) — down-soft */}
        <div style={{
          width: `${slWidth}%`,
          background: 'var(--down-soft)',
          borderLeft: '2px solid var(--down)',
          borderRadius: '1px 0 0 1px',
        }} />

        {/* Current price marker */}
        <div style={{
          width: 2,
          background: currentIsSafe ? 'var(--ink)' : 'var(--up)',
          flexShrink: 0,
        }} />

        {/* TP side (right) — up-soft */}
        <div style={{
          flex: 1,
          background: 'var(--up-soft)',
          borderRight: '2px solid var(--up)',
          borderRadius: '0 1px 1px 0',
        }} />
      </div>

      {/* Width hint */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        fontFamily: 'var(--font-mono)',
        fontSize: 10,
        color: 'var(--faint)',
      }}>
        <span>{slWidth.toFixed(0)}%</span>
        <span>{tpWidth.toFixed(0)}%</span>
      </div>
    </div>
  );
}
