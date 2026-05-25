// 04.2 AI 當沖 · K 線標記 — Wave 3-H
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import AppChrome from '../../components/AppChrome';
import Pill from '../../components/Pill';
import Eyebrow from '../../components/Eyebrow';
import Button from '../../components/Button';
import { useWebSocket } from '../../hooks/useWebSocket';
import { api } from '../../api';
import type { ChartView, AIMark, Tick } from '../../types';

// ── Mock data ───────────────────────────────────────────────────
function buildMockTicks(): Tick[] {
  const base = 960;
  const ticks: Tick[] = [];
  let open = base;
  for (let i = 0; i < 48; i++) {
    const close = open + (Math.random() - 0.48) * 15;
    const high = Math.max(open, close) + Math.random() * 6;
    const low = Math.min(open, close) - Math.random() * 6;
    const h = Math.floor(9 + i / 8);
    const m = (i % 8) * 5;
    ticks.push({
      t: `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`,
      open: +open.toFixed(1),
      high: +high.toFixed(1),
      low: +low.toFixed(1),
      close: +close.toFixed(1),
      volume: Math.floor(100 + Math.random() * 500),
    });
    open = close;
  }
  return ticks;
}

const MOCK_TICKS = buildMockTicks();

function buildMA(ticks: Tick[], period: number): number[] {
  return ticks.map((_, i) => {
    if (i < period - 1) return NaN;
    const slice = ticks.slice(i - period + 1, i + 1);
    return slice.reduce((s, t) => s + t.close, 0) / period;
  });
}

function buildRSI(ticks: Tick[], period = 14): number[] {
  const rsi: number[] = new Array(ticks.length).fill(NaN);
  if (ticks.length < period + 1) return rsi;
  let gains = 0, losses = 0;
  for (let i = 1; i <= period; i++) {
    const diff = ticks[i].close - ticks[i - 1].close;
    if (diff >= 0) gains += diff; else losses -= diff;
  }
  let avgGain = gains / period, avgLoss = losses / period;
  rsi[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < ticks.length; i++) {
    const diff = ticks[i].close - ticks[i - 1].close;
    avgGain = (avgGain * (period - 1) + (diff >= 0 ? diff : 0)) / period;
    avgLoss = (avgLoss * (period - 1) + (diff < 0 ? -diff : 0)) / period;
    rsi[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return rsi;
}

const MOCK_MARKS: AIMark[] = [
  { index: 2,  time: '09:10', kind: 'buy',  label: 'BUY',  confidence: 0.86, reasoning: '突破 MA20，成交量放大' },
  { index: 12, time: '10:00', kind: 'add',  label: 'ADD',  confidence: 0.78, reasoning: '拉回 MA20 支撐確認' },
  { index: 24, time: '11:00', kind: 'warn', label: 'WARN', confidence: 0.65, reasoning: 'RSI 進入超買，注意回調' },
  { index: 36, time: '12:00', kind: 'tp',   label: 'TP',   confidence: 0.90, reasoning: '接近目標價，可考慮減碼' },
  { index: 42, time: '12:30', kind: 'note', label: 'NOTE', confidence: 0.72, reasoning: '大盤轉弱，持續觀察' },
];

function buildMockChart(code: string): ChartView {
  const ticks = MOCK_TICKS;
  const ma20 = buildMA(ticks, 20);
  const ma5 = buildMA(ticks, 5);
  return {
    code,
    ticks,
    ma20,
    ma5,
    bollinger: { upper: ma20.map((v) => v + 20), mid: ma20, lower: ma20.map((v) => v - 20) },
    rsi: buildRSI(ticks),
    ai_marks: MOCK_MARKS,
    next_action_suggestion: {
      kind: 'hold',
      text: '目前價格在 MA20 上方，RSI 71.4 接近超買區間。建議持倉觀察，若 RSI > 75 可考慮減碼 50%。',
      confidence: 0.78,
      suggested_value: undefined,
    },
  };
}

// ── Mark config ─────────────────────────────────────────────────
const markColor: Record<string, string> = {
  buy:    'var(--up)',
  sell:   'var(--down)',
  add:    'var(--up)',
  reduce: 'var(--down)',
  warn:   'var(--gold)',
  tp:     'var(--down)',
  sl:     'var(--up)',
  note:   'var(--muted)',
};

// ── SVG KChart ──────────────────────────────────────────────────
interface KChartProps {
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

function KChart({
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

// ── Legend ──────────────────────────────────────────────────────
function ChartLegend({ ma20Valid: _ma20Valid }: { ma20Valid: boolean }) {
  return (
    <div style={{ display: 'flex', gap: 14, padding: '6px 8px', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', borderBottom: '1px solid var(--hair)', flexWrap: 'wrap' }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 12, height: 3, background: 'var(--gold)', display: 'inline-block' }} />
        MA20
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 12, height: 3, background: 'var(--up)', display: 'inline-block', borderTop: '1px dashed var(--up)' }} />
        TP
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 12, height: 3, background: 'var(--down)', display: 'inline-block', borderTop: '1px dashed var(--down)' }} />
        SL
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 10, height: 10, background: 'var(--up)', display: 'inline-block' }} />
        AI 進場
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 10, height: 10, background: 'var(--gold)', display: 'inline-block' }} />
        AI 警告
      </span>
    </div>
  );
}

// ── Adjust modal ─────────────────────────────────────────────────
function AdjustModal({
  type,
  code,
  current,
  onClose,
  onSave,
}: {
  type: 'tp' | 'sl';
  code: string;
  current: number;
  onClose: () => void;
  onSave: (value: number) => Promise<void>;
}) {
  const [val, setVal] = useState(String(current));
  const [saving, setSaving] = useState(false);
  const label = type === 'tp' ? '停利價' : '停損價';
  const color = type === 'tp' ? 'var(--up)' : 'var(--down)';

  const handleSave = async () => {
    const num = parseFloat(val);
    if (isNaN(num)) return;
    setSaving(true);
    try {
      await onSave(num);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(0,0,0,0.5)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000,
    }}>
      <div style={{
        background: 'var(--surface)',
        border: `1px solid ${color}`,
        padding: 24, minWidth: 300,
      }}>
        <div style={{ fontSize: 13, fontWeight: 500, color, marginBottom: 12 }}>
          調整 {code} {label}
        </div>
        <input
          type="number"
          value={val}
          onChange={(e) => setVal(e.target.value)}
          step={0.5}
          style={{
            width: '100%', height: 38, padding: '0 10px',
            fontFamily: 'var(--font-mono)', fontSize: 14,
            border: `1px solid ${color}`,
            background: 'var(--panel)', color: 'var(--ink)',
            boxSizing: 'border-box',
          }}
          autoFocus
        />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 14 }}>
          <button onClick={onClose} style={{ padding: '6px 14px', fontSize: 12, cursor: 'pointer', background: 'var(--surface)', border: '1px solid var(--hair-2)', fontFamily: 'inherit' }}>
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{ padding: '6px 14px', fontSize: 12, cursor: 'pointer', background: color, color: '#fff', border: 'none', fontFamily: 'inherit' }}
          >
            {saving ? '儲存中…' : '確認'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main ────────────────────────────────────────────────────────
export default function ChartPage() {
  const { code = '2330' } = useParams<{ code: string }>();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const [chartWidth, setChartWidth] = useState(700);
  const [chartData, setChartData] = useState<ChartView>(buildMockChart(code));
  const [selectedMarkIdx, setSelectedMarkIdx] = useState<number | null>(null);
  const [adjustModal, setAdjustModal] = useState<'tp' | 'sl' | null>(null);
  const [showCloseConfirm, setShowCloseConfirm] = useState(false);
  const [closingPos, setClosingPos] = useState(false);

  // WS live updates
  const { data: wsData } = useWebSocket<ChartView>(`/ws/daytrade/${code}/chart`);
  useEffect(() => {
    if (wsData) setChartData(wsData);
  }, [wsData]);

  // Initial fetch
  useEffect(() => {
    api.getChartData(code).then(setChartData).catch(() => {/* use mock */});
    setChartData(buildMockChart(code));
  }, [code]);

  // Measure container width
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setChartWidth(el.clientWidth);
    });
    ro.observe(el);
    setChartWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const ticks = chartData.ticks;
  const last = ticks[ticks.length - 1];
  const first = ticks[0];
  const lastClose = last?.close ?? 0;
  const openP = first?.open ?? 0;
  const highP = Math.max(...ticks.map((t) => t.high));
  const lowP = Math.min(...ticks.map((t) => t.low));
  const totalVol = ticks.reduce((s, t) => s + t.volume, 0);
  const changePct = openP > 0 ? ((lastClose - openP) / openP) * 100 : 0;
  const isUp = lastClose >= openP;

  // Mock position metadata
  const targetPrice = 1010;
  const stopLossPrice = 940;
  const monitoringMinutes = 87;
  const confidence = 0.86;
  const distTp = ((targetPrice - lastClose) / lastClose) * 100;
  const distSl = ((lastClose - stopLossPrice) / lastClose) * 100;

  void (selectedMarkIdx !== null
    ? chartData.ai_marks.find((m) => m.index === selectedMarkIdx)
    : null);

  const handleAdjustTp = useCallback(async (value: number) => {
    await api.adjustTp(code, value);
  }, [code]);

  const handleAdjustSl = useCallback(async (value: number) => {
    await api.adjustSl(code, value);
  }, [code]);

  const handleClose = useCallback(async () => {
    setClosingPos(true);
    try {
      await api.closePick(code);
      navigate('/daytrade');
    } finally {
      setClosingPos(false);
    }
  }, [code, navigate]);

  return (
    <AppChrome title={`K 線標記 · ${code}`} eyebrow="04.2">
      {/* Identity strip */}
      <div style={{
        background: 'var(--surface)',
        borderBottom: '1px solid var(--hair)',
        padding: '10px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 20,
        flexShrink: 0,
      }}>
        {/* Left: code + name + price + OHLV */}
        <div style={{ minWidth: 240 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 2 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: 16, color: 'var(--ink)' }}>
              {code}
            </span>
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>台積電</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 4 }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1',
              fontSize: 22, fontWeight: 500,
              color: isUp ? 'var(--up)' : 'var(--down)',
            }}>
              {lastClose.toFixed(1)}
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: isUp ? 'var(--up)' : 'var(--down)' }}>
              {isUp ? '+' : ''}{changePct.toFixed(2)}%
            </span>
          </div>
          <div style={{ display: 'flex', gap: 10, fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted-2)' }}>
            <span>O {openP.toFixed(1)}</span>
            <span>H {highP.toFixed(1)}</span>
            <span>L {lowP.toFixed(1)}</span>
            <span>V {totalVol.toLocaleString()}</span>
          </div>
        </div>

        {/* Pills */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <Pill color="var(--gold)" bg="var(--gold-soft)" border="var(--gold)" size={10}>
            監控 {monitoringMinutes}m
          </Pill>
          <Pill
            color={confidence >= 0.75 ? 'var(--up)' : confidence >= 0.6 ? 'var(--gold)' : 'var(--muted)'}
            bg={confidence >= 0.75 ? 'var(--up-soft)' : confidence >= 0.6 ? 'var(--gold-soft)' : 'transparent'}
            border={confidence >= 0.75 ? 'var(--up)' : confidence >= 0.6 ? 'var(--gold)' : 'var(--hair)'}
            size={10}
          >
            AI {(confidence * 100).toFixed(0)}%
          </Pill>
          <Pill color="var(--down)" bg="var(--down-soft)" border="var(--down)" size={10}>
            距TP +{distTp.toFixed(2)}%
          </Pill>
          <Pill color="var(--up)" bg="var(--up-soft)" border="var(--up)" size={10}>
            距SL -{distSl.toFixed(2)}%
          </Pill>
        </div>

        <div style={{ flex: 1 }} />

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <Button size="small" onClick={() => setAdjustModal('tp')}>
            調整停利
          </Button>
          <Button size="small" onClick={() => setAdjustModal('sl')}>
            調整停損
          </Button>
          <Button size="small" variant="danger" onClick={() => setShowCloseConfirm(true)}>
            立即平倉
          </Button>
        </div>
      </div>

      {/* Main body: left chart + right column */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left: chart */}
        <div ref={containerRef} style={{ flex: 1, overflow: 'auto', background: 'var(--surface)', borderRight: '1px solid var(--hair)' }}>
          <ChartLegend ma20Valid={chartData.ma20.some((v) => !isNaN(v))} />
          <KChart
            ticks={ticks}
            ma20={chartData.ma20}
            rsi={chartData.rsi}
            targetPrice={targetPrice}
            stopLossPrice={stopLossPrice}
            aiMarks={chartData.ai_marks}
            selectedMarkIndex={selectedMarkIdx}
            onSelectMark={setSelectedMarkIdx}
            width={chartWidth}
          />
        </div>

        {/* Right column: 320px */}
        <div style={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--surface)' }}>
          {/* AI mark log */}
          <div style={{ borderBottom: '1px solid var(--hair)', padding: '8px 12px', flexShrink: 0 }}>
            <Eyebrow label="AI 進出場標記" right={
              <Pill color="var(--muted)" bg="transparent" border="var(--hair)" size={10}>
                {chartData.ai_marks.length} 個
              </Pill>
            } />
          </div>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {chartData.ai_marks.map((mark) => {
              const color = markColor[mark.kind] ?? 'var(--muted)';
              const isSelected = selectedMarkIdx === mark.index;
              return (
                <div
                  key={mark.index}
                  onClick={() => setSelectedMarkIdx(isSelected ? null : mark.index)}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 8,
                    padding: '8px 12px',
                    borderBottom: '1px solid var(--hair)',
                    borderLeft: `3px solid ${color}`,
                    background: isSelected ? 'var(--surface-2)' : 'transparent',
                    cursor: 'pointer',
                  }}
                >
                  {/* Icon box */}
                  <div style={{
                    width: 22, height: 22, background: color,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    flexShrink: 0,
                  }}>
                    <span style={{ fontSize: 8, color: '#fff', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                      {mark.label}
                    </span>
                  </div>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                      <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--ink)' }}>
                        {mark.label} — {mark.kind.toUpperCase()}
                      </span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--muted)' }}>
                        {mark.time}
                      </span>
                    </div>
                    {mark.reasoning && (
                      <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
                        {mark.reasoning}
                      </div>
                    )}
                    <div style={{ marginTop: 3 }}>
                      <Pill
                        color={mark.confidence >= 0.75 ? 'var(--up)' : mark.confidence >= 0.6 ? 'var(--gold)' : 'var(--muted)'}
                        bg="transparent"
                        border="var(--hair)"
                        size={10}
                      >
                        conf {(mark.confidence * 100).toFixed(0)}%
                      </Pill>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Next action suggestion */}
          {chartData.next_action_suggestion && (
            <div style={{
              borderTop: '1px solid var(--hair)',
              borderLeft: '3px solid var(--up)',
              background: 'var(--surface)',
              padding: '12px 14px',
              flexShrink: 0,
            }}>
              <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 6 }}>
                建議下一步
              </div>
              <div style={{ fontSize: 12, color: 'var(--ink)', lineHeight: 1.6, marginBottom: 8 }}>
                {chartData.next_action_suggestion.text}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <Pill
                  color={chartData.next_action_suggestion.confidence >= 0.75 ? 'var(--up)' : 'var(--gold)'}
                  bg="transparent" border="var(--hair)" size={10}
                >
                  AI 信心 {(chartData.next_action_suggestion.confidence * 100).toFixed(0)}%
                </Pill>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <Button
                  size="small"
                  variant="primary"
                  onClick={() => {
                    const s = chartData.next_action_suggestion;
                    if (!s) return;
                    if (s.kind === 'adjust_tp' && s.suggested_value) api.adjustTp(code, s.suggested_value);
                    if (s.kind === 'adjust_sl' && s.suggested_value) api.adjustSl(code, s.suggested_value);
                  }}
                >
                  套用
                </Button>
                <Button size="small" variant="ghost">忽略</Button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Adjust modal */}
      {adjustModal && (
        <AdjustModal
          type={adjustModal}
          code={code}
          current={adjustModal === 'tp' ? targetPrice : stopLossPrice}
          onClose={() => setAdjustModal(null)}
          onSave={adjustModal === 'tp' ? handleAdjustTp : handleAdjustSl}
        />
      )}

      {/* Close confirm */}
      {showCloseConfirm && (
        <div style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.5)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 1000,
        }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--up)', padding: 24, minWidth: 320 }}>
            <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--up)', marginBottom: 8 }}>
              立即平倉 {code}
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
              將以市價立即平倉此持倉，操作不可撤銷，並發送 Telegram 通知。
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Button variant="default" onClick={() => setShowCloseConfirm(false)}>取消</Button>
              <Button variant="danger" disabled={closingPos} onClick={handleClose}>
                {closingPos ? '平倉中…' : '確認平倉'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </AppChrome>
  );
}
