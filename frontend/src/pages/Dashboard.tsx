// 02 Dashboard 總覽 — Wave 2-F 完整實作
import React, { useState, useEffect, useCallback } from 'react';
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
import type { DaytradeLive, TopNRun, Alert, Pick } from '../types';

// ─── Mock data ────────────────────────────────────────────────────────────────

const MOCK_SPARK_PNL = [320, 580, 410, 760, 840, 720, 950, 1100, 1080, 1234];

const MOCK_CHART_TAIEX: number[] = [
  21050, 21080, 21120, 21090, 21140, 21180, 21220, 21200,
  21235, 21210, 21240, 21230, 21250, 21235, 21240,
];
const MOCK_CHART_PORTFOLIO: number[] = [
  100000, 100420, 100820, 100640, 101100, 101440, 101820, 101580,
  102100, 101900, 102400, 102200, 102500, 102340, 102480,
];

const MOCK_PICKS: Pick[] = [
  {
    code: '2330', name: '台積電', sector: '半導體',
    signal: 'buy', confidence: 0.86,
    target_price: 1185, stop_loss_price: 1050, last_price: 1135, change_pct: 1.34,
    spark: [1090, 1105, 1098, 1115, 1120, 1110, 1128, 1135],
    reason: 'AI 訊號強力看多，外資連買三日', tags: ['黃金交叉', '量比 1.8x'],
    action: 'approved', budget: 113500, budget_ratio: 0.15,
    run_id: 'plan-2026-05-24', created_at: '2026-05-24T08:30:00Z',
  },
  {
    code: '2454', name: '聯發科', sector: '半導體',
    signal: 'buy', confidence: 0.81,
    target_price: 1350, stop_loss_price: 1200, last_price: 1270, change_pct: 0.95,
    spark: [1240, 1248, 1255, 1250, 1260, 1265, 1268, 1270],
    reason: 'KD 黃金交叉，MACD 翻正，外資買超', tags: ['KD 交叉', 'MACD 翻正'],
    action: 'approved', budget: 101600, budget_ratio: 0.13,
    run_id: 'plan-2026-05-24', created_at: '2026-05-24T08:30:00Z',
  },
  {
    code: '2382', name: '廣達', sector: 'AI 伺服器',
    signal: 'buy', confidence: 0.78,
    target_price: 320, stop_loss_price: 270, last_price: 295, change_pct: 2.07,
    spark: [275, 280, 285, 278, 290, 288, 292, 295],
    reason: 'AI 伺服器需求強勁，上升通道完整', tags: ['上升通道', 'AI 概念'],
    action: 'pending', budget: 59000, budget_ratio: 0.08,
    run_id: 'plan-2026-05-24', created_at: '2026-05-24T08:30:00Z',
  },
  {
    code: '2317', name: '鴻海', sector: '電子製造',
    signal: 'buy', confidence: 0.72,
    target_price: 230, stop_loss_price: 195, last_price: 210, change_pct: -0.47,
    spark: [212, 210, 208, 211, 210, 209, 210, 210],
    reason: '跌深反彈訊號，殖利率支撐', tags: ['殖利率支撐', 'RSI 超賣'],
    action: 'observe', budget: 42000, budget_ratio: 0.06,
    run_id: 'plan-2026-05-24', created_at: '2026-05-24T08:30:00Z',
  },
  {
    code: '2308', name: '台達電', sector: '電源管理',
    signal: 'buy', confidence: 0.76,
    target_price: 390, stop_loss_price: 340, last_price: 362, change_pct: 0.83,
    spark: [355, 358, 356, 360, 362, 359, 361, 362],
    reason: '電動車 + AI 電源雙驅動，量增有撐', tags: ['電動車', '量比 1.4x'],
    action: 'approved', budget: 72400, budget_ratio: 0.10,
    run_id: 'plan-2026-05-24', created_at: '2026-05-24T08:30:00Z',
  },
];

const MOCK_ALERTS: Alert[] = [
  {
    id: 'a1', time: '10:41:55', level: 'high',
    code: '2330', name: '台積電', text: '接近目標價 1185，建議觀察出場時機',
    kind: 'target_hit', resolved: false, source: 'monitor', telegram_sent: true,
  },
  {
    id: 'a2', time: '10:38:12', level: 'med',
    code: '2454', name: '聯發科', text: '成交量異常放大 2.1x，確認趨勢強度',
    kind: 'note', resolved: false, source: 'monitor', telegram_sent: false,
  },
  {
    id: 'a3', time: '10:25:30', level: 'high',
    code: '2382', name: '廣達', text: '突破前高 292，AI 訊號轉強',
    kind: 'stop_warn', resolved: false, source: 'ai', telegram_sent: true,
  },
  {
    id: 'a4', time: '10:15:44', level: 'low',
    code: '2317', name: '鴻海', text: 'RSI 回至 50，中性訊號，觀察',
    kind: 'note', resolved: true, source: 'monitor', telegram_sent: false,
  },
  {
    id: 'a5', time: '09:55:08', level: 'med',
    code: '2308', name: '台達電', text: '跳空開盤，確認缺口支撐有效',
    kind: 'note', resolved: false, source: 'ai', telegram_sent: false,
  },
  {
    id: 'a6', time: '09:32:20', level: 'high',
    code: '2330', name: '台積電', text: '開盤強勢突破，AI 信心升至 0.88',
    kind: 'tp', resolved: true, source: 'ai', telegram_sent: true,
  },
];

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

  const picks: Pick[] = topN?.picks?.slice(0, 5) ?? MOCK_PICKS;
  const alerts: Alert[] = liveData?.alerts?.slice(0, 6) ?? MOCK_ALERTS;

  // KPI data from live or mock
  const pnl = liveData?.net_pnl ?? 12340;
  const pnlPct = liveData
    ? liveData.net_pnl / (liveData.risk?.budget ?? 1200000) * 100
    : 0.74;
  const freeCash = liveData?.risk?.free ?? 363600;
  const budget = liveData?.risk?.budget ?? 1200000;
  const cashRatioPct = (freeCash / budget * 100).toFixed(1);
  const tradeCount = liveData ? liveData.positions.length + liveData.closed_count : 8;
  const buySide = liveData?.positions?.filter(p => p.side === 'buy').length ?? 6;
  const sellSide = liveData?.positions?.filter(p => p.side === 'sell').length ?? 2;

  const avgConfidence = picks.length > 0
    ? picks.reduce((acc, p) => acc + p.confidence, 0) / picks.length
    : 0.74;
  const highConfCount = picks.filter(p => p.confidence >= 0.75).length;

  const countdownSec = liveData?.countdown_seconds ?? getSecondsToForceClose(now);
  const countdownStr = formatCountdownHMS(countdownSec);
  const isArmed = countdownSec > 0 && countdownSec < 7200; // within 2 hours

  // Chart data
  const chartData: ChartData = {
    taiex: MOCK_CHART_TAIEX,
    portfolio: MOCK_CHART_PORTFOLIO,
    labels: generateMockLabels(timeRange),
    markers: [
      { index: 2, kind: 'B' },
      { index: 8, kind: 'TP' },
    ],
  };

  // KPI sparkline
  const kpiSpark = MOCK_SPARK_PNL;

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
                  {avgConfidence.toFixed(2)}
                </span>
              }
              sub={`≥ 0.75 共 ${highConfCount} 檔`}
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
              <DashChart data={chartData} />
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
