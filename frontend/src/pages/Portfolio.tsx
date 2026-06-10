// 05.1 持倉 / 投資組合 — Wave 4-J
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import AppChrome from '../components/AppChrome';
import Kpi from '../components/Kpi';
import Card from '../components/Card';
import Pill from '../components/Pill';
import { api } from '../api';
import { getSecondsToForceClose } from '../lib/time';
import type { PortfolioSummary, Position, SectorAllocation } from '../types';

const MOCK_POSITIONS: Position[] = [
  {
    code: '2330', name: '台積電', sector: '半導體',
    side: 'buy', entry_price: 1100, last_price: 1135, quantity: 1,
    lot: 'common', cost: 110000, market_value: 113500,
    pnl: 3500, pnl_pct: 3.18,
    target_price: 1185, stop_loss_price: 1050,
    distance_to_tp_pct: 4.41, distance_to_sl_pct: 7.49,
    thread_state: 'monitoring', confidence: 0.86,
    opened_at: '2026-05-29T09:15:00Z',
  },
  {
    code: '2454', name: '聯發科', sector: '半導體',
    side: 'buy', entry_price: 1250, last_price: 1270, quantity: 1,
    lot: 'common', cost: 125000, market_value: 127000,
    pnl: 2000, pnl_pct: 1.60,
    target_price: 1350, stop_loss_price: 1200,
    distance_to_tp_pct: 6.30, distance_to_sl_pct: 5.51,
    thread_state: 'monitoring', confidence: 0.81,
    opened_at: '2026-05-29T09:22:00Z',
  },
  {
    code: '2382', name: '廣達', sector: 'AI 伺服器',
    side: 'buy', entry_price: 290, last_price: 295, quantity: 2,
    lot: 'common', cost: 58000, market_value: 59000,
    pnl: 1000, pnl_pct: 1.72,
    target_price: 320, stop_loss_price: 270,
    distance_to_tp_pct: 8.47, distance_to_sl_pct: 8.47,
    thread_state: 'monitoring', confidence: 0.78,
    opened_at: '2026-05-29T09:30:00Z',
  },
  {
    code: '2308', name: '台達電', sector: '電源管理',
    side: 'buy', entry_price: 360, last_price: 362, quantity: 2,
    lot: 'common', cost: 72000, market_value: 72400,
    pnl: 400, pnl_pct: 0.56,
    target_price: 390, stop_loss_price: 340,
    distance_to_tp_pct: 7.73, distance_to_sl_pct: 6.08,
    thread_state: 'monitoring', confidence: 0.76,
    opened_at: '2026-05-29T09:45:00Z',
  },
];

const MOCK_SECTORS: SectorAllocation[] = [
  { name: '半導體', ratio: 0.48, limit: 0.50, value: 240500 },
  { name: 'AI 伺服器', ratio: 0.16, limit: 0.30, value: 59000 },
  { name: '電源管理', ratio: 0.19, limit: 0.30, value: 72400 },
  { name: '現金', ratio: 0.17, limit: 1.0, value: 63600 },
];

const MOCK_RECENT_PNL: { date: string; pnl: number }[] = [
  { date: '5/16', pnl: 4200 },
  { date: '5/17', pnl: -1800 },
  { date: '5/18', pnl: 3100 },
  { date: '5/19', pnl: -2400 },
  { date: '5/20', pnl: 6800 },
  { date: '5/21', pnl: 1200 },
  { date: '5/22', pnl: -800 },
  { date: '5/23', pnl: 5400 },
  { date: '5/24', pnl: 3600 },
  { date: '5/25', pnl: -1200 },
  { date: '5/26', pnl: 7200 },
  { date: '5/27', pnl: 2800 },
  { date: '5/28', pnl: -1600 },
  { date: '5/29', pnl: 6900 },
];

const MOCK_CUMULATIVE: { dates: string[]; me: number[]; index: number[] } = {
  dates: ['5/1', '5/5', '5/8', '5/12', '5/15', '5/19', '5/22', '5/26', '5/29'],
  me:    [100000, 102400, 105800, 103200, 108600, 112400, 115800, 119200, 121600],
  index: [100000, 101200, 102400, 101000, 103200, 104400, 105600, 106800, 107200],
};

const MOCK_PORTFOLIO: PortfolioSummary = {
  net_value: 435500,
  budget: 500000,
  free_cash: 63600,
  cash_ratio: 0.146,
  unrealized_pnl: 6900,
  realized_pnl: 18420,
  net_pnl: 25320,
  net_pnl_pct: 0.0618,
  position_count: 4,
  closed_today: 2,
  countdown_seconds: getSecondsToForceClose(),
  positions: MOCK_POSITIONS,
  sector_breakdown: MOCK_SECTORS,
  recent_pnl_days: MOCK_RECENT_PNL,
  cumulative_vs_index: MOCK_CUMULATIVE,
  alpha_mtd: 0.0154,
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmt(n: number, prefix = ''): string {
  const abs = Math.abs(n);
  const formatted = abs >= 1000
    ? abs.toLocaleString('en-US', { maximumFractionDigits: 0 })
    : abs.toFixed(0);
  return (n < 0 ? '-' : n > 0 ? '+' : '') + prefix + formatted;
}

function fmtCountdown(seconds: number): string {
  if (seconds <= 0) return '已收盤';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// ─── Donut SVG ────────────────────────────────────────────────────────────────

const SECTOR_COLORS = ['var(--up)', 'var(--gold)', 'var(--down)', 'var(--muted)'];

function DonutChart({ sectors }: { sectors: SectorAllocation[] }) {
  const R = 50;
  const cx = 70;
  const cy = 60;
  const strokeW = 16;
  const circumference = 2 * Math.PI * R;

  let offset = 0;
  const slices = sectors.map((s, i) => {
    const dashLen = s.ratio * circumference;
    const dashGap = circumference - dashLen;
    const rotate = (offset / circumference) * 360 - 90;
    offset += dashLen;
    return { ...s, dashLen, dashGap, rotate, color: SECTOR_COLORS[i % SECTOR_COLORS.length] };
  });

  return (
    <svg width={200} height={120} viewBox="0 0 200 120">
      {slices.map((sl, i) => (
        <circle
          key={i}
          cx={cx} cy={cy} r={R}
          fill="none"
          stroke={sl.color}
          strokeWidth={strokeW}
          strokeDasharray={`${sl.dashLen} ${sl.dashGap}`}
          strokeDashoffset={0}
          transform={`rotate(${sl.rotate}, ${cx}, ${cy})`}
        />
      ))}
      {/* Legend */}
      {slices.map((sl, i) => (
        <g key={i} transform={`translate(145, ${10 + i * 24})`}>
          <rect x={0} y={0} width={8} height={8} fill={sl.color} />
          <text x={12} y={8} fontSize={9} fontFamily="var(--font-mono)" fill="var(--ink-2)">
            {sl.name}
          </text>
          <text x={12} y={18} fontSize={8} fontFamily="var(--font-mono)" fill="var(--muted)">
            {(sl.ratio * 100).toFixed(0)}%
          </text>
        </g>
      ))}
    </svg>
  );
}

// ─── PnL Bar Chart (14 days) ──────────────────────────────────────────────────

function PnlBars({ days }: { days: { date: string; pnl: number }[] }) {
  const W = 280;
  const H = 80;
  const barW = Math.floor(W / days.length) - 2;
  const maxAbs = Math.max(...days.map(d => Math.abs(d.pnl)), 1);

  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block' }}>
      {/* Center line */}
      <line x1={0} y1={H / 2} x2={W} y2={H / 2} stroke="var(--hair)" strokeWidth={1} />

      {days.map((d, i) => {
        const x = i * (barW + 2);
        const isUp = d.pnl >= 0;
        const barH = Math.abs(d.pnl) / maxAbs * (H / 2 - 4);
        const y = isUp ? H / 2 - barH : H / 2;
        return (
          <g key={i}>
            <rect
              x={x} y={y}
              width={barW} height={Math.max(barH, 1)}
              fill={isUp ? 'var(--up)' : 'var(--down)'}
              opacity={0.85}
            />
          </g>
        );
      })}

      {/* Date labels — first and last */}
      <text x={0} y={H} fontSize={8} fontFamily="var(--font-mono)" fill="var(--muted)">
        {days[0]?.date}
      </text>
      <text x={W} y={H} fontSize={8} fontFamily="var(--font-mono)" fill="var(--muted)" textAnchor="end">
        {days[days.length - 1]?.date}
      </text>
    </svg>
  );
}

// ─── Cumulative vs Index line chart ───────────────────────────────────────────

function CumulativeChart({
  data,
  alpha,
}: {
  data: { dates: string[]; me: number[]; index: number[] };
  alpha: number;
}) {
  const W = 280;
  const H = 100;
  const PAD = { top: 12, right: 8, bottom: 20, left: 8 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const allVals = [...data.me, ...data.index];
  const minV = Math.min(...allVals);
  const maxV = Math.max(...allVals);
  const range = maxV - minV || 1;

  const toX = (i: number) => PAD.left + (i / Math.max(data.dates.length - 1, 1)) * innerW;
  const toY = (v: number) => PAD.top + (1 - (v - minV) / range) * innerH;

  const mePoints = data.me.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ');
  const idxPoints = data.index.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(' ');

  return (
    <div>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: 4,
      }}>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 9,
          color: 'var(--muted)',
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
        }}>
          本月累計 vs 大盤
        </span>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontFeatureSettings: '"tnum" 1',
          fontSize: 11,
          fontWeight: 600,
          color: alpha >= 0 ? 'var(--up)' : 'var(--down)',
        }}>
          α {alpha >= 0 ? '+' : ''}{(alpha * 100).toFixed(2)}%
        </span>
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block' }}>
        {/* Index — gray dashed */}
        <polyline
          points={idxPoints}
          fill="none"
          stroke="var(--muted)"
          strokeWidth={1}
          strokeDasharray="3 3"
        />
        {/* Me — red solid */}
        <polyline
          points={mePoints}
          fill="none"
          stroke="var(--up)"
          strokeWidth={1.5}
        />
        {/* X-axis labels */}
        {data.dates.map((d, i) => {
          const step = Math.floor(data.dates.length / 3) || 1;
          if (i % step !== 0 && i !== data.dates.length - 1) return null;
          return (
            <text key={i} x={toX(i)} y={H} fontSize={8}
              fontFamily="var(--font-mono)" fill="var(--muted)" textAnchor="middle">
              {d}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

// ─── Position table row ────────────────────────────────────────────────────────

function PositionRow({ pos, idx, onClick }: { pos: Position; idx: number; onClick: () => void }) {
  const isUp = pos.pnl >= 0;
  const pnlColor = isUp ? 'var(--up)' : 'var(--down)';

  // TP/SL distance bar
  const totalDist = pos.distance_to_tp_pct + pos.distance_to_sl_pct;
  const slRatio = totalDist > 0 ? pos.distance_to_sl_pct / totalDist : 0.5;

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onClick()}
      style={{
        display: 'grid',
        gridTemplateColumns: '80px 56px 64px 64px 40px 72px 80px 90px 56px',
        padding: '0 12px',
        gap: 6,
        alignItems: 'center',
        borderBottom: '1px solid var(--hair)',
        cursor: 'pointer',
        background: idx % 2 === 1 ? 'var(--surface-2)' : 'transparent',
        minHeight: 44,
      }}
    >
      {/* 標的 */}
      <div>
        <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: 11 }}>{pos.code}</div>
        <div style={{ fontSize: 10, color: 'var(--muted)' }}>{pos.name}</div>
      </div>

      {/* 單位 */}
      <Pill
        color="var(--ink-2)"
        bg="var(--surface-2)"
        border="var(--hair)"
        size={10}
      >
        {pos.lot === 'common' ? '整張' : '零股'}
      </Pill>

      {/* 進場 */}
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontFeatureSettings: '"tnum" 1' }}>
        {pos.entry_price.toFixed(0)}
      </span>

      {/* 現價 */}
      <span style={{
        fontFamily: 'var(--font-mono)', fontSize: 11,
        fontFeatureSettings: '"tnum" 1', fontWeight: 500,
      }}>
        {pos.last_price.toFixed(0)}
      </span>

      {/* 數量 */}
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, textAlign: 'right' }}>
        {pos.quantity}
      </span>

      {/* 市值 */}
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontFeatureSettings: '"tnum" 1', textAlign: 'right' }}>
        {(pos.market_value / 1000).toFixed(1)}K
      </span>

      {/* 損益 */}
      <div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontFeatureSettings: '"tnum" 1', color: pnlColor, fontWeight: 500 }}>
          {fmt(pos.pnl)}
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: pnlColor }}>
          {isUp ? '+' : ''}{pos.pnl_pct.toFixed(2)}%
        </div>
      </div>

      {/* TP/SL 距離 bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <div style={{ flex: 1, height: 4, background: 'var(--hair)', position: 'relative', overflow: 'hidden' }}>
          {/* SL side (left) */}
          <div style={{
            position: 'absolute', left: 0, top: 0,
            width: `${slRatio * 100}%`, height: '100%',
            background: 'var(--down-soft)',
          }} />
          {/* Current position marker */}
          <div style={{
            position: 'absolute', left: `${slRatio * 100}%`, top: -1,
            width: 2, height: 6,
            background: isUp ? 'var(--up)' : 'var(--down)',
            transform: 'translateX(-50%)',
          }} />
        </div>
      </div>

      {/* 狀態 */}
      <Pill
        color={pos.thread_state === 'monitoring' ? 'var(--down)' : 'var(--muted)'}
        bg={pos.thread_state === 'monitoring' ? 'var(--down-soft)' : 'transparent'}
        border={pos.thread_state === 'monitoring' ? 'var(--down)' : 'var(--hair)'}
        size={10}
      >
        {pos.thread_state === 'monitoring' ? '監控中' : pos.thread_state}
      </Pill>
    </div>
  );
}

// ─── Sector bar strip ─────────────────────────────────────────────────────────

function SectorBarStrip({ sectors }: { sectors: SectorAllocation[] }) {
  const total = sectors.reduce((acc, s) => acc + s.ratio, 0) || 1;
  return (
    <div style={{ display: 'flex', height: 24, overflow: 'hidden', borderTop: '1px solid var(--hair)' }}>
      {sectors.map((s, i) => (
        <div
          key={i}
          title={`${s.name} ${(s.ratio * 100).toFixed(0)}%`}
          style={{
            width: `${(s.ratio / total) * 100}%`,
            background: SECTOR_COLORS[i % SECTOR_COLORS.length],
            opacity: 0.7,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 9,
            fontFamily: 'var(--font-mono)',
            color: 'white',
            overflow: 'hidden',
            borderRight: '1px solid var(--surface)',
          }}
        >
          {s.name}
        </div>
      ))}
    </div>
  );
}

// ─── Portfolio ────────────────────────────────────────────────────────────────

export default function Portfolio() {
  const navigate = useNavigate();
  const [data, setData] = useState<PortfolioSummary>(MOCK_PORTFOLIO);
  const [countdown, setCountdown] = useState(getSecondsToForceClose());

  useEffect(() => {
    api.getPortfolio()
      .then(setData)
      .catch(() => setData(MOCK_PORTFOLIO));
  }, []);

  // Countdown tick
  useEffect(() => {
    const id = setInterval(() => setCountdown(s => Math.max(0, s - 1)), 1000);
    return () => clearInterval(id);
  }, []);

  const { unrealized_pnl, realized_pnl, net_value, free_cash, position_count } = data;

  return (
    <AppChrome title="持倉 / 投資組合" eyebrow="05.1">
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>

        {/* ── 6-col KPI strip ──────────────────── */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(6, 1fr)',
          borderBottom: '1px solid var(--hair)',
          flexShrink: 0,
        }}>
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="總淨值"
              value={<span style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1' }}>
                {(net_value / 10000).toFixed(1)}萬
              </span>}
            />
          </div>
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="可用資金"
              value={<span style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1' }}>
                {free_cash.toLocaleString('en-US')}
              </span>}
              sub={`${(data.cash_ratio * 100).toFixed(1)}% 現金`}
            />
          </div>
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="未實現損益"
              value={<span style={{ color: unrealized_pnl >= 0 ? 'var(--up)' : 'var(--down)' }}>
                {fmt(unrealized_pnl)}
              </span>}
              valueColor={unrealized_pnl >= 0 ? 'var(--up)' : 'var(--down)'}
            />
          </div>
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="已實現損益"
              value={<span style={{ color: realized_pnl >= 0 ? 'var(--up)' : 'var(--down)' }}>
                {fmt(realized_pnl)}
              </span>}
              valueColor={realized_pnl >= 0 ? 'var(--up)' : 'var(--down)'}
            />
          </div>
          <div style={{ borderRight: '1px solid var(--hair)' }}>
            <Kpi
              label="持倉數"
              value={`${position_count} 檔`}
              sub={`今日已平 ${data.closed_today} 筆`}
            />
          </div>
          <div>
            <Kpi
              label="距強制平倉"
              value={<span style={{
                fontFamily: 'var(--font-mono)',
                fontFeatureSettings: '"tnum" 1, "zero" 1',
                color: countdown < 1800 ? 'var(--up)' : 'var(--ink)',
              }}>
                {fmtCountdown(countdown)}
              </span>}
            />
          </div>
        </div>

        {/* ── Main body 2-col ──────────────────── */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1.6fr 1fr',
          gap: 0,
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
        }}>
          {/* ── Left col ─────────────────────── */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            borderRight: '1px solid var(--hair)',
            overflow: 'hidden',
          }}>
            {/* Table header */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: '80px 56px 64px 64px 40px 72px 80px 90px 56px',
              padding: '6px 12px',
              gap: 6,
              borderBottom: '1px solid var(--hair)',
              fontFamily: 'var(--font-mono)',
              fontSize: 9,
              letterSpacing: '0.10em',
              textTransform: 'uppercase',
              color: 'var(--muted-2)',
              flexShrink: 0,
            }}>
              <span>標的</span>
              <span>單位</span>
              <span>進場</span>
              <span>現價</span>
              <span style={{ textAlign: 'right' }}>張</span>
              <span style={{ textAlign: 'right' }}>市值</span>
              <span>損益</span>
              <span>TP/SL</span>
              <span>狀態</span>
            </div>

            {/* Positions */}
            <div style={{ flex: 1, overflowY: 'auto' }}>
              {data.positions.map((pos, idx) => (
                <PositionRow
                  key={pos.code}
                  pos={pos}
                  idx={idx}
                  onClick={() => navigate(`/daytrade/${pos.code}/chart`)}
                />
              ))}
              {data.positions.length === 0 && (
                <div style={{
                  padding: 24, textAlign: 'center',
                  color: 'var(--muted)', fontSize: 12,
                }}>
                  目前無持倉
                </div>
              )}
            </div>

            {/* Sector bar strip */}
            <SectorBarStrip sectors={data.sector_breakdown} />
          </div>

          {/* ── Right col ────────────────────── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0, overflow: 'auto' }}>

            {/* Card 1: 板塊配置 donut */}
            <Card label="板塊配置" padding={12}>
              <DonutChart sectors={data.sector_breakdown} />
            </Card>

            {/* Card 2: 近 14 日損益 */}
            <Card label="近 14 日損益" padding={12} style={{ borderTop: 'none' }}>
              <PnlBars days={data.recent_pnl_days} />
            </Card>

            {/* Card 3: 本月累計 vs 大盤 */}
            <Card label="本月累計 vs 大盤" padding={12} style={{ borderTop: 'none' }}>
              <CumulativeChart data={data.cumulative_vs_index} alpha={data.alpha_mtd} />
            </Card>
          </div>
        </div>
      </div>
    </AppChrome>
  );
}
