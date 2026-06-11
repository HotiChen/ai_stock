// Shared component — SVG K-line + RSI chart
// Extracted from pages/daytrade/Chart.tsx (KChart)
import { useMemo } from 'react';
import type { Tick, AIMark } from '../types';

// ── Mark color map (used by both KChart and Chart.tsx sidebar) ───
// kept here since KChart owns the SVG rendering
export const markColor: Record<string, string> = {
  buy:    'var(--up)',
  sell:   'var(--down)',
  add:    'var(--up)',
  reduce: 'var(--down)',
  warn:   'var(--gold)',
  tp:     'var(--down)',
  sl:     'var(--up)',
  note:   'var(--muted)',
};

// ── Props ────────────────────────────────────────────────────────
export interface KChartProps {
  ticks: Tick[];
  ma20: number[];
  rsi: number[];
  targetPrice: number;
  stopLossPrice: number;
  aiMarks: AIMark[];
  selectedMarkIndex: number | null;
  onSelectMark: (idx: number | null) => void;
  width: number;
}

// ── Component ────────────────────────────────────────────────────
export default function KChart({
  ticks, ma20, rsi,
  targetPrice, stopLossPrice,
  aiMarks, selectedMarkIndex, onSelectMark,
  width,
}: KChartProps) {
  const CHART_H = 360;
  const RSI_H = 80;
  const PAD_L = 8;
  const PAD_R = 54;  // price axis
  const PAD_T = 16;
  const PAD_B = 24;  // time axis

  const chartW = width - PAD_L - PAD_R;
  const n = ticks.length;
  const candleW = Math.max(3, Math.floor(chartW / n) - 1);
  const candleStep = chartW / n;

  // Price range
  const validMa = ma20.filter((v) => !isNaN(v));
  const allPrices = ticks.flatMap((t) => [t.high, t.low]);
  if (validMa.length) allPrices.push(...validMa);
  allPrices.push(targetPrice, stopLossPrice);
  const priceMin = Math.min(...allPrices) * 0.995;
  const priceMax = Math.max(...allPrices) * 1.005;
  const priceRange = priceMax - priceMin || 1;

  const toY = (p: number, h: number = CHART_H) =>
    PAD_T + ((priceMax - p) / priceRange) * (h - PAD_T - PAD_B);

  // RSI range
  const validRsi = rsi.filter((v) => !isNaN(v));
  const rsiMin = 0, rsiMax = 100;
  const toRsiY = (v: number) => RSI_H * 0.1 + ((rsiMax - v) / (rsiMax - rsiMin)) * (RSI_H * 0.8);

  // Build MA20 path
  const maPath = useMemo(() => {
    const pts = ma20
      .map((v, i) => ({ v, i }))
      .filter(({ v }) => !isNaN(v));
    if (pts.length < 2) return '';
    return pts.map(({ v, i }, idx) => {
      const x = PAD_L + (i + 0.5) * candleStep;
      const y = toY(v);
      return `${idx === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  }, [ma20, ticks]);

  // Build RSI path
  const rsiPath = useMemo(() => {
    const pts = rsi.map((v, i) => ({ v, i })).filter(({ v }) => !isNaN(v));
    if (pts.length < 2) return '';
    return pts.map(({ v, i }, idx) => {
      const x = PAD_L + (i + 0.5) * candleStep;
      const y = toRsiY(v);
      return `${idx === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  }, [rsi, ticks]);

  const lastClose = ticks[ticks.length - 1]?.close ?? 0;
  const lastY = toY(lastClose);
  const tpY = toY(targetPrice);
  const slY = toY(stopLossPrice);

  // Time axis labels — show every 8 ticks
  const timeLabels: { x: number; label: string }[] = [];
  ticks.forEach((t, i) => {
    if (i % 8 === 0) {
      timeLabels.push({ x: PAD_L + (i + 0.5) * candleStep, label: t.t });
    }
  });

  // Price axis labels
  const priceSteps = 6;
  const priceLabels: { y: number; price: number }[] = [];
  for (let i = 0; i <= priceSteps; i++) {
    const price = priceMin + (priceRange * i) / priceSteps;
    priceLabels.push({ y: toY(price), price });
  }

  return (
    <div>
      {/* K-line chart */}
      <svg width={width} height={CHART_H} style={{ display: 'block', background: 'var(--surface)' }}>
        {/* Grid lines */}
        {priceLabels.map(({ y, price }) => (
          <g key={price}>
            <line x1={PAD_L} y1={y} x2={PAD_L + chartW} y2={y} stroke="var(--hair)" strokeWidth={0.5} />
            <text x={PAD_L + chartW + 4} y={y + 4} fontSize={9} fill="var(--muted-2)" fontFamily="var(--font-mono)">
              {price.toFixed(0)}
            </text>
          </g>
        ))}

        {/* TP dashed line */}
        <line
          x1={PAD_L} y1={tpY} x2={PAD_L + chartW} y2={tpY}
          stroke="var(--up)" strokeWidth={1} strokeDasharray="4 4"
        />
        <text x={PAD_L + chartW + 4} y={tpY + 4} fontSize={9} fill="var(--up)" fontFamily="var(--font-mono)">
          TP {targetPrice.toFixed(0)}
        </text>

        {/* SL dashed line */}
        <line
          x1={PAD_L} y1={slY} x2={PAD_L + chartW} y2={slY}
          stroke="var(--down)" strokeWidth={1} strokeDasharray="4 4"
        />
        <text x={PAD_L + chartW + 4} y={slY + 4} fontSize={9} fill="var(--down)" fontFamily="var(--font-mono)">
          SL {stopLossPrice.toFixed(0)}
        </text>

        {/* Current price dashed */}
        <line
          x1={PAD_L} y1={lastY} x2={PAD_L + chartW} y2={lastY}
          stroke="var(--ink)" strokeWidth={1} strokeDasharray="2 2" opacity={0.4}
        />
        {/* Current price label box */}
        <rect x={PAD_L + chartW + 2} y={lastY - 8} width={48} height={14} fill="var(--up)" />
        <text x={PAD_L + chartW + 26} y={lastY + 3} fontSize={9} fill="#fff" fontFamily="var(--font-mono)" textAnchor="middle">
          {lastClose.toFixed(1)}
        </text>

        {/* MA20 line */}
        {maPath && (
          <path d={maPath} fill="none" stroke="var(--gold)" strokeWidth={1.5} />
        )}

        {/* Candles */}
        {ticks.map((t, i) => {
          const x = PAD_L + i * candleStep;
          const cx = x + candleStep / 2;
          const isUp = t.close >= t.open;
          const color = isUp ? 'var(--up)' : 'var(--down)';
          const bodyTop = toY(Math.max(t.open, t.close));
          const bodyBot = toY(Math.min(t.open, t.close));
          const bodyH = Math.max(1, bodyBot - bodyTop);
          const wickTop = toY(t.high);
          const wickBot = toY(t.low);

          return (
            <g key={i}>
              {/* Wick */}
              <line x1={cx} y1={wickTop} x2={cx} y2={wickBot} stroke={color} strokeWidth={1} />
              {/* Body */}
              <rect
                x={cx - candleW / 2} y={bodyTop}
                width={candleW} height={bodyH}
                fill={color}
              />
            </g>
          );
        })}

        {/* AI marks */}
        {aiMarks.map((mark) => {
          if (mark.index >= ticks.length) return null;
          const t = ticks[mark.index];
          const x = PAD_L + mark.index * candleStep + candleStep / 2;
          const y = toY(t.high) - 14;
          const color = markColor[mark.kind] ?? 'var(--muted)';
          const isSelected = selectedMarkIndex === mark.index;

          return (
            <g key={mark.index} onClick={() => onSelectMark(isSelected ? null : mark.index)} style={{ cursor: 'pointer' }}>
              <rect
                x={x - 10} y={y - 10} width={20} height={14}
                fill={color} opacity={isSelected ? 1 : 0.8}
                stroke={isSelected ? 'var(--ink)' : 'none'}
                strokeWidth={1}
              />
              <text x={x} y={y - 1} fontSize={8} fill="#fff" textAnchor="middle" fontFamily="var(--font-mono)" fontWeight={600}>
                {mark.label}
              </text>
              {/* Leader line */}
              <line x1={x} y1={y + 4} x2={x} y2={toY(t.high) - 2} stroke={color} strokeWidth={0.8} />
            </g>
          );
        })}

        {/* Time axis */}
        <line x1={PAD_L} y1={CHART_H - PAD_B} x2={PAD_L + chartW} y2={CHART_H - PAD_B} stroke="var(--hair)" />
        {timeLabels.map(({ x, label }) => (
          <text key={label} x={x} y={CHART_H - PAD_B + 12} fontSize={9} fill="var(--muted-2)" textAnchor="middle" fontFamily="var(--font-mono)">
            {label}
          </text>
        ))}
      </svg>

      {/* RSI sub-chart */}
      <svg width={width} height={RSI_H} style={{ display: 'block', background: 'var(--surface)', borderTop: '1px solid var(--hair)' }}>
        {/* 70 / 50 / 30 lines */}
        {[70, 50, 30].map((v) => {
          const y = toRsiY(v);
          const isExtreme = v !== 50;
          return (
            <g key={v}>
              <line x1={PAD_L} y1={y} x2={PAD_L + chartW} y2={y}
                stroke={isExtreme ? (v === 70 ? 'var(--up)' : 'var(--down)') : 'var(--hair)'}
                strokeWidth={0.8} strokeDasharray={isExtreme ? '3 3' : ''}
              />
              <text x={PAD_L + chartW + 4} y={y + 3} fontSize={8} fill="var(--muted-2)" fontFamily="var(--font-mono)">
                {v}
              </text>
            </g>
          );
        })}

        {/* RSI line */}
        {rsiPath && (
          <path d={rsiPath} fill="none" stroke="var(--muted)" strokeWidth={1.5} />
        )}

        {/* RSI label */}
        <text x={PAD_L + 4} y={12} fontSize={9} fill="var(--muted-2)" fontFamily="var(--font-mono)" fontWeight={600}>
          RSI(14)
        </text>

        {/* Current RSI value */}
        {validRsi.length > 0 && (() => {
          const lastRsi = validRsi[validRsi.length - 1];
          const lastRsiIdx = rsi.map((v, i) => ({ v, i })).filter(({ v }) => !isNaN(v)).at(-1)?.i ?? 0;
          const x = PAD_L + (lastRsiIdx + 0.5) * candleStep;
          return (
            <text x={x + 4} y={toRsiY(lastRsi) + 3} fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">
              {lastRsi.toFixed(1)}
            </text>
          );
        })()}
      </svg>
    </div>
  );
}
