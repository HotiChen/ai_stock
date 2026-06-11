// Shared component — Backtest equity curve SVG
// Extracted from pages/Simulate.tsx (BacktestEquitySVG)

// ── Props ────────────────────────────────────────────────────────
export interface EquityCurveProps {
  data: { date: string; me: number; index: number }[];
}

// ── Component ────────────────────────────────────────────────────
export default function EquityCurve({ data }: EquityCurveProps) {
  if (!data.length) return null;
  const W = 600, H = 200;
  const pad = { t: 12, r: 12, b: 24, l: 60 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const allVals = data.flatMap(d => [d.me, d.index]);
  const minV = Math.min(...allVals), maxV = Math.max(...allVals);
  const range = maxV - minV || 1;
  const toX = (i: number) => pad.l + (i / (data.length - 1)) * iw;
  const toY = (v: number) => pad.t + ih - ((v - minV) / range) * ih;
  const mePath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(d.me).toFixed(1)}`).join(' ');
  const indexPath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(d.index).toFixed(1)}`).join(' ');
  const fillPath = `${mePath} L${toX(data.length - 1)},${H - pad.b} L${pad.l},${H - pad.b} Z`;
  const yTicks = [minV, minV + range * 0.5, maxV];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H }}>
      <defs>
        <linearGradient id="equity-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--up)" stopOpacity="0.15" />
          <stop offset="100%" stopColor="var(--up)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {yTicks.map(v => (
        <g key={v}>
          <line x1={pad.l} y1={toY(v)} x2={W - pad.r} y2={toY(v)} stroke="var(--border)" strokeWidth={0.5} />
          <text x={pad.l - 4} y={toY(v) + 4} textAnchor="end" fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">
            {(v / 10000).toFixed(0)}萬
          </text>
        </g>
      ))}
      <path d={fillPath} fill="url(#equity-grad)" />
      <path d={indexPath} stroke="var(--muted)" strokeWidth={1} fill="none" strokeDasharray="4,3" />
      <path d={mePath} stroke="var(--up)" strokeWidth={2} fill="none" />
      {[0, Math.floor(data.length / 2), data.length - 1].map(i => (
        <text key={i} x={toX(i)} y={H - pad.b + 14} textAnchor="middle" fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">
          {data[i]?.date.slice(5)}
        </text>
      ))}
      <text x={pad.l + 8} y={pad.t + 14} fontSize={9} fill="var(--up)" fontFamily="var(--font-mono)">策略</text>
      <text x={pad.l + 40} y={pad.t + 14} fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">- - 大盤</text>
    </svg>
  );
}
