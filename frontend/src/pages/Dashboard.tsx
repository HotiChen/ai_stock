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
import { api, isMockData } from '../api';
import type { DaytradeLive, TopNRun, Alert, Pick, PortfolioSummary } from '../types';

// 本頁一律使用真實 API 資料。先前這裡有一整組 MOCK_* 常數，會在沒有資料時
// 靜默頂替，導致畫面出現看似真實的假推薦與假資金曲線——已全部移除。
// 沒有資料時請顯示空狀態（見 EmptyHint），不要編造。

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

function generateMockLabels(range: TimeRange): string[] {
  if (range === '1D') {
    return ['09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
            '12:00', '12:30', '13:00', '13:30', '13:30'];
  }
  if (range === '5D') {
    return ['週一', '週一', '週二', '週二', '週三', '週三',
            '週四', '週四', '週五', '週五', '週五'];
  }
  return ['5/1', '5/5', '5/8', '5/12', '5/15', '5/19',
          '5/22', '5/24', '5/26', '5/28', '5/31'];
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate();
  const [timeRange, setTimeRange] = useState<TimeRange>('1D');
  const [topN, setTopN] = useState<TopNRun | null>(null);
  const [topNLoading, setTopNLoading] = useState(true);
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);
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

  // 投組彙總：提供資金曲線、近日損益 sparkline，以及盤後的 KPI 來源
  useEffect(() => {
    api.getPortfolio()
      .then(setPortfolio)
      .catch(() => setPortfolio(null));
  }, []);

  // 一律以真實資料為準；沒有資料就呈現空狀態。
  //
  // 這裡**刻意不**退回 MOCK_PICKS／MOCK_ALERTS。這是一個下單系統，畫面上出現
  // 「2330 台積電 買進 信心 0.82」而實際上沒有任何預測，比顯示一片空白危險得多
  // ——使用者可能照著假訊號操作。同理，下方 KPI 的預設值一律用 0 而不是
  // 12340／363600／1200000 這類看起來像真的的數字。
  // 後端查不到真實資料時會回示範資料（X-Data-Source: mock）。那批推薦是寫死的
  // 2330／2454／2382，但會被 Shioaji 的真實報價填充，看起來與真訊號無異。
  // 一律當成「沒有資料」處理。
  const topNIsMock = isMockData(topN);
  const picks: Pick[] = topNIsMock ? [] : (topN?.picks?.slice(0, 5) ?? []);
  const alerts: Alert[] = liveData?.alerts?.slice(0, 6) ?? [];
  const hasPicks = picks.length > 0;

  // KPI：優先用 daytrade/live（盤中即時），退而求其次用 portfolio（收盤後彙總）
  const pnl = liveData?.net_pnl ?? portfolio?.net_pnl ?? 0;
  const budget = liveData?.risk?.budget ?? portfolio?.budget ?? 0;
  const pnlPct = budget > 0 ? (pnl / budget) * 100 : 0;
  const freeCash = liveData?.risk?.free ?? portfolio?.free_cash ?? 0;
  const cashRatioPct = budget > 0 ? ((freeCash / budget) * 100).toFixed(1) : '0.0';
  const tradeCount = liveData
    ? liveData.positions.length + liveData.closed_count
    : (portfolio ? portfolio.position_count + portfolio.closed_today : 0);
  const buySide = liveData?.positions?.filter(p => p.side === 'buy').length ?? 0;
  const sellSide = liveData?.positions?.filter(p => p.side === 'sell').length ?? 0;

  // 沒有 picks 就沒有信心均值可言——回 null，由 UI 顯示「—」而不是編一個 0.74
  const avgConfidence = hasPicks
    ? picks.reduce((acc, p) => acc + p.confidence, 0) / picks.length
    : null;
  const highConfCount = picks.filter(p => p.confidence >= 0.75).length;

  const countdownSec = liveData?.countdown_seconds ?? getSecondsToForceClose(now);
  const countdownStr = formatCountdownHMS(countdownSec);
  const isArmed = countdownSec > 0 && countdownSec < 7200; // within 2 hours

  // 圖表：portfolio.cumulative_vs_index 是後端算好的「投組淨值 vs 大盤」序列
  const cumulative = portfolio?.cumulative_vs_index;
  const hasChart = !!cumulative && cumulative.dates.length > 0;
  const chartData: ChartData = {
    taiex: cumulative?.index ?? [],
    portfolio: cumulative?.me ?? [],
    labels: cumulative?.dates ?? [],
    markers: [],
  };

  // KPI sparkline：近幾日損益
  const kpiSpark = portfolio?.recent_pnl_days?.map(d => d.pnl) ?? [];

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
                <span style={{ color: pnl >= 0 ? 'var(--up)' : 'var(--down)' }}>
                  {pnl >= 0 ? '+' : ''}{fmt(pnl)}
                </span>
              }
              sub={
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Pill
                    color={pnlPct >= 0 ? 'var(--up)' : 'var(--down)'}
                    bg={pnlPct >= 0 ? 'var(--up-soft)' : 'var(--down-soft)'}
                    border={pnlPct >= 0 ? 'var(--up)' : 'var(--down)'}
                    size={10}
                  >
                    {fmtPct(pnlPct)}
                  </Pill>
                </span>
              }
              spark={kpiSpark}
              valueColor={pnl >= 0 ? 'var(--up)' : 'var(--down)'}
            />
          </div>

          {/* 欄 2: 可用資金 */}
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="可用資金"
              value={
                <span style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1' }}>
                  NT$ {freeCash.toLocaleString('en-US')}
                </span>
              }
              sub={`${cashRatioPct}% 現金部位`}
            />
          </div>

          {/* 欄 3: 今日已執行 */}
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="今日已執行"
              value={`${tradeCount} 筆`}
              sub={`買 ${buySide} / 賣 ${sellSide}`}
            />
          </div>

          {/* 欄 4: AI 信心均值 */}
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="AI 信心均值"
              value={
                <span style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1' }}>
                  {avgConfidence === null ? '—' : avgConfidence.toFixed(2)}
                </span>
              }
              sub={hasPicks ? `≥ 0.75 共 ${highConfCount} 檔` : '今日尚無預測'}
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
              {hasChart
                ? <DashChart data={chartData} />
                : <EmptyHint text="尚無淨值資料，累積交易後顯示" />}
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
                : !hasPicks
                ? <EmptyHint text={topNIsMock
                    ? '後端回傳示範資料，已隱藏。資料庫尚無今日預測'
                    : '今日尚無預測，08:30 盤前分析後產生'} />
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

/**
 * 空狀態提示。
 *
 * 存在的理由：這是一個下單系統。先前 picks/alerts 在沒有資料時會靜默退回
 * MOCK 常數，畫面照樣列出「2330 台積電 信心 0.82」，但那是寫死的示範資料，
 * 不是任何一次真實預測。使用者無從分辨，可能照著假訊號操作。
 * 寧可明確顯示「今日尚無預測」，也不要用看起來合理的假資料填滿版面。
 */
function EmptyHint({ text }: { text: string }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100%',
      minHeight: 80,
      padding: '20px 12px',
      color: 'var(--ink-3, #888)',
      fontSize: 12,
      letterSpacing: '0.02em',
      textAlign: 'center',
    }}>
      {text}
    </div>
  );
}
