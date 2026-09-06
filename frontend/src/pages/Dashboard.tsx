// 02 Dashboard 總覽 — Wave 2-F 完整實作
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import AppChrome from '../components/AppChrome';
import Kpi from '../components/Kpi';
import Eyebrow from '../components/Eyebrow';
import Pill from '../components/Pill';
import Sparkline from '../components/Sparkline';
import ConfidenceTick from '../components/ConfidenceTick';
import AlertRow from '../components/AlertRow';
import { useWebSocket } from '../hooks/useWebSocket';
import { api } from '../api';
import DataState from '../components/DataState';
import type { DaytradeLive, TopNRun, Alert, Pick } from '../types';

// ─── Mock data ────────────────────────────────────────────────────────────────

// ─── ChartData types ──────────────────────────────────────────────────────────

interface ChartData {
  taiex: number[];
  portfolio: number[];
  labels: string[];
  markers: { index: number; kind: 'B' | 'TP' }[];
}

type TimeRange = '1D' | '5D' | '1M';

// ─── DashChart component ──────────────────────────────────────────────────────

interface DashChartProps {
  data: ChartData;
}

function DashChart({ data }: DashChartProps) {
  const W = 600;
  const H = 200;
  const PADDING = { top: 16, right: 56, bottom: 28, left: 8 };

  const innerW = W - PADDING.left - PADDING.right;
  const innerH = H - PADDING.top - PADDING.bottom;

  const allVals = [...data.taiex, ...data.portfolio];
  const minV = Math.min(...allVals);
  const maxV = Math.max(...allVals);
  const range = maxV - minV || 1;

  const toX = (i: number, total: number) =>
    PADDING.left + (i / Math.max(total - 1, 1)) * innerW;
  const toY = (v: number) =>
    PADDING.top + (1 - (v - minV) / range) * innerH;

  const portfolioPoints = data.portfolio
    .map((v, i) => `${toX(i, data.portfolio.length).toFixed(1)},${toY(v).toFixed(1)}`)
    .join(' ');
  const taiexPoints = data.taiex
    .map((v, i) => `${toX(i, data.taiex.length).toFixed(1)},${toY(v).toFixed(1)}`)
    .join(' ');

  // Current price horizontal dashed line (portfolio last value)
  const lastPortfolio = data.portfolio[data.portfolio.length - 1];
  const currentY = toY(lastPortfolio).toFixed(1);
  const lastTaiex = data.taiex[data.taiex.length - 1];

  // Y-axis labels (3 ticks)
  const yTicks = [minV, (minV + maxV) / 2, maxV];

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      style={{ display: 'block', overflow: 'visible' }}
      aria-label="大盤 vs 投組走勢圖"
    >
      {/* Grid lines */}
      {yTicks.map((v, i) => (
        <line
          key={i}
          x1={PADDING.left}
          y1={toY(v)}
          x2={W - PADDING.right}
          y2={toY(v)}
          stroke="var(--hair)"
          strokeWidth={1}
        />
      ))}

      {/* Current price dashed line */}
      <line
        x1={PADDING.left}
        y1={currentY}
        x2={W - PADDING.right}
        y2={currentY}
        stroke="var(--up)"
        strokeWidth={1}
        strokeDasharray="3 3"
        opacity={0.5}
      />

      {/* TAIEX — gray dashed line */}
      <polyline
        points={taiexPoints}
        fill="none"
        stroke="var(--muted)"
        strokeWidth={1.25}
        strokeDasharray="4 4"
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* Portfolio — red solid line */}
      <polyline
        points={portfolioPoints}
        fill="none"
        stroke="var(--up)"
        strokeWidth={1.75}
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* B / TP markers */}
      {data.markers.map((m, i) => {
        const x = toX(m.index, data.portfolio.length);
        const y = toY(data.portfolio[m.index] ?? lastPortfolio);
        const isB = m.kind === 'B';
        return (
          <g key={i}>
            <rect
              x={x - 8}
              y={y - 18}
              width={16}
              height={14}
              fill={isB ? 'var(--up)' : 'var(--gold)'}
              rx={1}
            />
            <text
              x={x}
              y={y - 8}
              textAnchor="middle"
              fill="white"
              fontSize={9}
              fontFamily="var(--font-mono)"
              fontWeight={600}
            >
              {m.kind}
            </text>
          </g>
        );
      })}

      {/* Y-axis price labels (right side) */}
      {yTicks.map((v, i) => (
        <text
          key={i}
          x={W - PADDING.right + 4}
          y={toY(v) + 4}
          fill="var(--muted)"
          fontSize={9}
          fontFamily="var(--font-mono)"
        >
          {v >= 1000 ? v.toFixed(0) : v.toFixed(0)}
        </text>
      ))}

      {/* Right-side current price label */}
      <rect
        x={W - PADDING.right + 2}
        y={Number(currentY) - 8}
        width={50}
        height={14}
        fill="var(--up)"
        rx={1}
      />
      <text
        x={W - PADDING.right + 27}
        y={Number(currentY) + 3}
        textAnchor="middle"
        fill="white"
        fontSize={9}
        fontFamily="var(--font-mono)"
        fontWeight={600}
      >
        {lastPortfolio >= 1000
          ? (lastPortfolio / 1000).toFixed(1) + 'K'
          : lastPortfolio.toFixed(0)}
      </text>

      {/* X-axis labels */}
      {data.labels.map((label, i) => {
        // Show ~5 labels evenly spaced
        const step = Math.floor(data.labels.length / 5) || 1;
        if (i % step !== 0 && i !== data.labels.length - 1) return null;
        return (
          <text
            key={i}
            x={toX(i, data.labels.length)}
            y={H - 4}
            textAnchor="middle"
            fill="var(--muted)"
            fontSize={9}
            fontFamily="var(--font-mono)"
          >
            {label}
          </text>
        );
      })}

      {/* Legend (top-left) */}
      <line x1={8} y1={8} x2={24} y2={8} stroke="var(--up)" strokeWidth={1.75} />
      <text x={28} y={12} fill="var(--ink)" fontSize={10} fontFamily="var(--font-mono)">
        投組 {lastPortfolio >= 1000
          ? (lastPortfolio / 1000).toFixed(2) + 'K'
          : lastPortfolio.toFixed(0)}
      </text>
      <line x1={90} y1={8} x2={106} y2={8} stroke="var(--muted)" strokeWidth={1.25} strokeDasharray="4 4" />
      <text x={110} y={12} fill="var(--muted)" fontSize={10} fontFamily="var(--font-mono)">
        加權 {lastTaiex.toFixed(0)}
      </text>
    </svg>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmt(n: number): string {
  return Math.abs(n) >= 1000
    ? (n < 0 ? '-' : '') + Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: 0 })
    : String(n);
}

function fmtPct(n: number): string {
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function formatCountdownHMS(seconds: number): string {
  if (seconds <= 0) return '00:00:00';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function getSecondsToForceClose(now: Date): number {
  const close = new Date(now);
  close.setHours(13, 25, 0, 0);
  return Math.max(0, Math.floor((close.getTime() - now.getTime()) / 1000));
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate();
  const [timeRange, setTimeRange] = useState<TimeRange>('1D');
  const [topN, setTopN] = useState<TopNRun | null>(null);
  const [topNLoading, setTopNLoading] = useState(true);
  const [blinkArmed, setBlinkArmed] = useState(true);
  const [now, setNow] = useState(new Date());

  // Live WebSocket data
  const { data: liveData } = useWebSocket<DaytradeLive>('/ws/daytrade');

  // Countdown clock
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // ARMED blink
  useEffect(() => {
    const id = setInterval(() => setBlinkArmed(b => !b), 600);
    return () => clearInterval(id);
  }, []);

  // Fetch top-N picks
  useEffect(() => {
    setTopNLoading(true);
    api.getTopN()
      .then(setTopN)
      .catch(() => setTopN(null))
      .finally(() => setTopNLoading(false));
  }, []);

  const picks: Pick[] = topN?.picks?.slice(0, 5) ?? [];
  const alerts: Alert[] = liveData?.alerts?.slice(0, 6) ?? [];

  // 這些原本在沒有即時資料時 fallback 到寫死的數字（+12,340 損益、
  // 363,600 現金、8 筆交易…）。畫面因此永遠「看起來正常」，而且沒有任何
  // 方法能從畫面上分辨哪個是真的。沒有資料就顯示 "—"。
  const DASH = '—';
  const pnl = liveData?.net_pnl ?? null;
  const budget = liveData?.risk?.budget ?? null;
  const pnlPct = pnl !== null && budget ? (pnl / budget) * 100 : null;
  const freeCash = liveData?.risk?.free ?? null;
  const cashRatioPct = freeCash !== null && budget
    ? ((freeCash / budget) * 100).toFixed(1) : null;
  const tradeCount = liveData ? liveData.positions.length + liveData.closed_count : null;
  const buySide = liveData?.positions?.filter(p => p.side === 'buy').length ?? null;
  const sellSide = liveData?.positions?.filter(p => p.side === 'sell').length ?? null;

  const avgConfidence = picks.length > 0
    ? picks.reduce((acc, p) => acc + p.confidence, 0) / picks.length
    : null;
  const highConfCount = picks.filter(p => p.confidence >= 0.75).length;

  const countdownSec = liveData?.countdown_seconds ?? getSecondsToForceClose(now);
  const countdownStr = formatCountdownHMS(countdownSec);
  const isArmed = countdownSec > 0 && countdownSec < 7200; // within 2 hours

  // 走勢圖原本畫的是兩條寫死的曲線。後端還沒有這個端點（spec/BACKEND_MAPPING
  // 未定義 /api/dashboard/chart），畫假線比不畫更糟——不畫至少誠實。
  const chartData: ChartData | null = null;

  // KPI 迷你走勢圖同理：原本是寫死的 [320, 580, 410, …]
  const kpiSpark: number[] = [];

  return (
    <AppChrome title="Dashboard 總覽" eyebrow="02">
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
      }}>
        {/* ── KPI Strip ──────────────────────────── ~80px */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1.4fr 1fr 1fr 1fr 1fr',
          borderBottom: '1px solid var(--hair)',
          flexShrink: 0,
        }}>
          {/* 欄 1: 今日損益 */}
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="今日損益"
              value={
                <span style={{ color: pnl === null ? 'var(--muted)' : pnl >= 0 ? 'var(--up)' : 'var(--down)' }}>
                  {pnl === null ? DASH : `${pnl >= 0 ? '+' : ''}${fmt(pnl)}`}
                </span>
              }
              sub={
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Pill
                    color={(pnlPct ?? 0) >= 0 ? 'var(--up)' : 'var(--down)'}
                    bg={(pnlPct ?? 0) >= 0 ? 'var(--up-soft)' : 'var(--down-soft)'}
                    border={(pnlPct ?? 0) >= 0 ? 'var(--up)' : 'var(--down)'}
                    size={10}
                  >
                    {pnlPct === null ? DASH : fmtPct(pnlPct)}
                  </Pill>
                </span>
              }
              spark={kpiSpark}
              valueColor={pnl === null ? 'var(--muted)' : pnl >= 0 ? 'var(--up)' : 'var(--down)'}
            />
          </div>

          {/* 欄 2: 可用資金 */}
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="可用資金"
              value={
                <span style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1' }}>
                  {freeCash === null ? DASH : `NT$ ${freeCash.toLocaleString('en-US')}`}
                </span>
              }
              sub={cashRatioPct === null ? '等待即時資料' : `${cashRatioPct}% 現金部位`}
            />
          </div>

          {/* 欄 3: 今日已執行 */}
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="今日已執行"
              value={tradeCount === null ? DASH : `${tradeCount} 筆`}
              sub={buySide === null ? '等待即時資料' : `買 ${buySide} / 賣 ${sellSide}`}
            />
          </div>

          {/* 欄 4: AI 信心均值 */}
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="AI 信心均值"
              value={
                <span style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1' }}>
                  {avgConfidence === null ? DASH : avgConfidence.toFixed(2)}
                </span>
              }
              sub={avgConfidence === null ? '尚無今日選股' : `≥ 0.75 共 ${highConfCount} 檔`}
            />
          </div>

          {/* 欄 5: 距強制平倉 */}
          <div>
            <Kpi
              label="距強制平倉"
              value={
                <span style={{
                  color: isArmed ? 'var(--up)' : 'var(--ink)',
                  fontFamily: 'var(--font-mono)',
                  fontFeatureSettings: '"tnum" 1, "zero" 1',
                }}>
                  {countdownStr}
                </span>
              }
              sub={
                isArmed ? (
                  <span style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    padding: '2px 7px',
                    borderRadius: 2,
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10,
                    letterSpacing: '0.04em',
                    background: blinkArmed ? 'var(--up)' : 'var(--up-soft)',
                    color: blinkArmed ? 'white' : 'var(--up)',
                    border: '1px solid var(--up)',
                    transition: 'background 0.2s, color 0.2s',
                  }}>
                    ARMED
                  </span>
                ) : '尚未進入平倉警戒'
              }
            />
          </div>
        </div>

        {/* ── Main content: Chart + Side panels ─────── */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 340px',
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
        }}>
          {/* ── Left: Main chart ─────────────────── */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            borderRight: '1px solid var(--hair)',
            overflow: 'hidden',
          }}>
            {/* Chart header */}
            <div style={{
              padding: '10px 14px 8px',
              borderBottom: '1px solid var(--hair)',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              flexShrink: 0,
            }}>
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 10,
                letterSpacing: '0.14em',
                textTransform: 'uppercase',
                color: 'var(--muted)',
                fontWeight: 500,
              }}>
                大盤 · 加權指數 vs 投組淨值
              </span>
              <span style={{ flex: 1 }} />
              {/* Time range pills */}
              {(['1D', '5D', '1M'] as TimeRange[]).map(r => (
                <button
                  key={r}
                  onClick={() => setTimeRange(r)}
                  style={{
                    padding: '2px 8px',
                    borderRadius: 2,
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10,
                    letterSpacing: '0.04em',
                    cursor: 'pointer',
                    border: '1px solid var(--hair)',
                    background: timeRange === r ? 'var(--ink)' : 'var(--surface)',
                    color: timeRange === r ? 'white' : 'var(--muted)',
                    fontWeight: timeRange === r ? 600 : 400,
                    transition: 'background 0.15s, color 0.15s',
                  }}
                >
                  {r}
                </button>
              ))}
            </div>

            {/* SVG Chart */}
            <div style={{
              flex: 1,
              padding: '12px 8px 8px',
              overflow: 'hidden',
            }}>
              {chartData
                ? <DashChart data={chartData} />
                : <DataState compact title="走勢圖尚未接上後端"
                    detail="後端還沒有這個端點。原本這裡畫的是兩條寫死的曲線，與你的實際損益無關。" />}
            </div>
          </div>

          {/* ── Right: Top 5 + Alerts ─────────────── */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}>
            {/* Top 5 推薦清單 */}
            <div style={{
              flex: '0 0 auto',
              borderBottom: '1px solid var(--hair)',
              display: 'flex',
              flexDirection: 'column',
            }}>
              <div style={{ padding: '10px 12px 8px' }}>
                <Eyebrow
                  label="今日推薦"
                  right={
                    <Pill
                      color="var(--ink)"
                      bg="var(--surface-2)"
                      border="var(--hair)"
                      size={10}
                    >
                      TOP 5
                    </Pill>
                  }
                />
              </div>

              {/* Column header */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: '20px 60px 1fr 48px 52px 36px',
                padding: '2px 12px',
                gap: 6,
                fontFamily: 'var(--font-mono)',
                fontSize: 9,
                letterSpacing: '0.10em',
                textTransform: 'uppercase',
                color: 'var(--muted-2)',
                borderBottom: '1px solid var(--hair)',
              }}>
                <span>#</span>
                <span>標的</span>
                <span>板塊</span>
                <span style={{ textAlign: 'right' }}>現價</span>
                <span style={{ textAlign: 'right' }}>漲幅</span>
                <span style={{ textAlign: 'center' }}>AI</span>
              </div>

              {/* Rows */}
              {topNLoading
                ? Array.from({ length: 5 }).map((_, i) => (
                    <SkeletonRow key={i} />
                  ))
                : picks.map((pick, idx) => (
                    <PickRow
                      key={pick.code}
                      pick={pick}
                      rank={idx + 1}
                      onClick={() => navigate(`/predict/${pick.code}`)}
                    />
                  ))}
            </div>

            {/* 警報串流 */}
            <div style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}>
              <div style={{ padding: '10px 12px 8px', flexShrink: 0 }}>
                <Eyebrow
                  label="警報串流"
                  right={
                    <Pill
                      color="white"
                      bg="var(--up)"
                      border="var(--up)"
                      size={10}
                    >
                      LIVE
                    </Pill>
                  }
                />
              </div>
              <div style={{ flex: 1, overflowY: 'auto' }}>
                {alerts.map(alert => (
                  <AlertRow
                    key={alert.id}
                    alert={alert}
                    onClick={() => navigate('/daytrade')}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </AppChrome>
  );
}

// ─── PickRow ──────────────────────────────────────────────────────────────────

function PickRow({
  pick,
  rank,
  onClick,
}: {
  pick: Pick;
  rank: number;
  onClick: () => void;
}) {
  const isUp = pick.change_pct >= 0;
  const changeColor = isUp ? 'var(--up)' : 'var(--down)';

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}
      style={{
        display: 'grid',
        gridTemplateColumns: '20px 60px 1fr 48px 52px 36px',
        padding: '6px 12px',
        gap: 6,
        alignItems: 'center',
        borderBottom: '1px solid var(--hair)',
        cursor: 'pointer',
        background: rank % 2 === 0 ? 'var(--surface-2)' : 'transparent',
        minHeight: 38,
      }}
    >
      {/* Rank */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 10,
        color: 'var(--muted)',
        fontFeatureSettings: '"tnum" 1',
      }}>
        {String(rank).padStart(2, '0')}
      </span>

      {/* Code + Sparkline */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontWeight: 600,
          fontSize: 11,
          color: 'var(--ink)',
          letterSpacing: '0.04em',
        }}>
          {pick.code}
        </span>
        <Sparkline data={pick.spark} width={54} height={14} />
      </div>

      {/* Sector */}
      <span style={{
        fontSize: 10,
        color: 'var(--muted)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}>
        {pick.sector}
      </span>

      {/* Price */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1',
        fontSize: 11,
        color: 'var(--ink)',
        textAlign: 'right',
      }}>
        {pick.last_price.toFixed(0)}
      </span>

      {/* Change % */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1',
        fontSize: 10,
        color: changeColor,
        textAlign: 'right',
        fontWeight: 500,
      }}>
        {isUp ? '+' : ''}{pick.change_pct.toFixed(2)}%
      </span>

      {/* Confidence Tick */}
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        <ConfidenceTick value={pick.confidence} />
      </div>
    </div>
  );
}

// ─── SkeletonRow ──────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      padding: '8px 12px',
      borderBottom: '1px solid var(--hair)',
      minHeight: 38,
    }}>
      {[20, 54, 80, 40, 40, 28].map((w, i) => (
        <div
          key={i}
          style={{
            width: w,
            height: 10,
            background: 'var(--hair)',
            borderRadius: 1,
            flexShrink: 0,
          }}
        />
      ))}
    </div>
  );
}
