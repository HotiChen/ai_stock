// 07.2 手機 · 當沖實況 (iPhone 402×874)
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api';
import type { DaytradeLive, Position } from '../../types';

const MOCK_LIVE: DaytradeLive = {
  countdown_seconds: 2127,
  force_close_at: '13:25:00',
  monitoring_count: 3,
  closed_count: 2,
  unrealized_pnl: 9840,
  realized_pnl: 2500,
  net_pnl: 12340,
  net_value: 1012340,
  positions: [
    { code: '2330', name: '台積電', sector: '半導體', side: 'buy', entry_price: 1042, last_price: 1068, quantity: 1, lot: 'common', cost: 104200, market_value: 106800, pnl: 2600, pnl_pct: 0.025, target_price: 1120, stop_loss_price: 1030, distance_to_tp_pct: 0.049, distance_to_sl_pct: 0.036, thread_state: 'monitoring', confidence: 0.89, opened_at: '09:12:00' },
    { code: '2382', name: '廣達', sector: 'AI伺服器', side: 'buy', entry_price: 295, last_price: 305, quantity: 2, lot: 'common', cost: 59000, market_value: 61000, pnl: 2000, pnl_pct: 0.034, target_price: 320, stop_loss_price: 290, distance_to_tp_pct: 0.049, distance_to_sl_pct: 0.016, thread_state: 'monitoring', confidence: 0.81, opened_at: '09:18:00' },
    { code: '2308', name: '台達電', sector: 'AI伺服器', side: 'buy', entry_price: 382, last_price: 391, quantity: 1, lot: 'common', cost: 38200, market_value: 39100, pnl: 900, pnl_pct: 0.024, target_price: 405, stop_loss_price: 375, distance_to_tp_pct: 0.036, distance_to_sl_pct: 0.041, thread_state: 'monitoring', confidence: 0.77, opened_at: '09:24:00' },
    { code: '2454', name: '聯發科', sector: '半導體', side: 'buy', entry_price: 1240, last_price: 1218, quantity: 1, lot: 'common', cost: 124000, market_value: 121800, pnl: -2200, pnl_pct: -0.018, target_price: 1320, stop_loss_price: 1210, distance_to_tp_pct: 0.084, distance_to_sl_pct: 0.007, thread_state: 'monitoring', confidence: 0.71, opened_at: '09:35:00' },
    { code: '2317', name: '鴻海', sector: '電子零件', side: 'buy', entry_price: 155, last_price: 151.5, quantity: 2, lot: 'intraday_odd', cost: 31000, market_value: 30300, pnl: -700, pnl_pct: -0.023, target_price: 165, stop_loss_price: 148, distance_to_tp_pct: 0.09, distance_to_sl_pct: 0.023, thread_state: 'monitoring', confidence: 0.65, opened_at: '10:02:00' },
  ],
  alerts: [],
  threads: [],
  risk: { budget: 1000000, used: 421000, free: 579000, utilization: 0.421, intraday_pnl: 12340, intraday_pnl_pct: 0.0123, daily_max_dd_limit: -50000, sector_allocation: [], blacklist: [], single_max: { value: 200000, ratio: 0.2, limit: 0.3, ok: true } },
  next_poll_in_seconds: 28,
};

function SparklineSVG({ points, color }: { points: number[]; color: string }) {
  if (points.length < 2) return null;
  const min = Math.min(...points), max = Math.max(...points);
  const range = max - min || 1;
  const W = 80, H = 32;
  const pts = points.map((v, i) => `${(i / (points.length - 1)) * W},${H - ((v - min) / range) * H}`).join(' ');
  const fillPts = `${pts} ${W},${H} 0,${H}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: W, height: H }}>
      <polygon points={fillPts} fill={color} fillOpacity={0.1} />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}

function PositionCard({ pos }: { pos: Position }) {
  const isProfit = pos.pnl >= 0;
  const dpTp = (pos.distance_to_tp_pct * 100).toFixed(1);
  const dpSl = (pos.distance_to_sl_pct * 100).toFixed(1);
  const tier = pos.confidence >= 0.75 ? '高' : pos.confidence >= 0.60 ? '中' : '低';
  const tierColor = pos.confidence >= 0.75 ? 'var(--up)' : pos.confidence >= 0.60 ? '#d4a017' : 'var(--down)';

  return (
    <div style={{ background: 'var(--surface)', border: `1px solid ${isProfit ? 'var(--up)' : 'var(--down)'}`, borderRadius: 4, padding: 14, marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 700 }}>{pos.code}</span>
          <span style={{ fontSize: 13, color: 'var(--fg)', marginLeft: 6 }}>{pos.name}</span>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700, color: isProfit ? 'var(--up)' : 'var(--down)' }}>
            {isProfit ? '+' : ''}{pos.pnl.toLocaleString()}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: isProfit ? 'var(--up)' : 'var(--down)' }}>
            {isProfit ? '+' : ''}{(pos.pnl_pct * 100).toFixed(2)}%
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 800 }}>{pos.last_price.toLocaleString()}</div>
          <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>進場 {pos.entry_price.toLocaleString()} · {pos.quantity}{pos.lot === 'intraday_odd' ? '零' : ''}張</div>
        </div>
        <SparklineSVG points={[pos.entry_price, (pos.entry_price + pos.last_price) / 2, pos.last_price]} color={isProfit ? 'var(--up)' : 'var(--down)'} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
        <div style={{ background: 'rgba(200,51,43,0.08)', borderRadius: 2, padding: '5px 8px', textAlign: 'center' }}>
          <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 2, fontFamily: 'var(--font-mono)' }}>目標</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--up)', fontWeight: 700 }}>{pos.target_price.toLocaleString()}</div>
          <div style={{ fontSize: 9, color: 'var(--up)', fontFamily: 'var(--font-mono)' }}>+{dpTp}%</div>
        </div>
        <div style={{ background: 'rgba(46,125,79,0.08)', borderRadius: 2, padding: '5px 8px', textAlign: 'center' }}>
          <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 2, fontFamily: 'var(--font-mono)' }}>停損</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--down)', fontWeight: 700 }}>{pos.stop_loss_price.toLocaleString()}</div>
          <div style={{ fontSize: 9, color: 'var(--down)', fontFamily: 'var(--font-mono)' }}>-{dpSl}%</div>
        </div>
        <div style={{ background: 'var(--bg)', borderRadius: 2, padding: '5px 8px', textAlign: 'center' }}>
          <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 2, fontFamily: 'var(--font-mono)' }}>信心</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: tierColor, fontWeight: 700 }}>{tier}</div>
          <div style={{ fontSize: 9, color: tierColor, fontFamily: 'var(--font-mono)' }}>{Math.round(pos.confidence * 100)}%</div>
        </div>
      </div>
    </div>
  );
}

function CountdownBanner({ seconds }: { seconds: number }) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const isUrgent = seconds < 1800;
  return (
    <div style={{ background: isUrgent ? 'var(--down)' : '#1a1a2e', padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '2px solid var(--border)' }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: isUrgent ? '#fff' : '#888', letterSpacing: 1 }}>FORCE-CLOSE 13:25</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 800, color: isUrgent ? '#fff' : '#ccc' }}>
        {String(h).padStart(2, '0')}:{String(m).padStart(2, '0')}:{String(s).padStart(2, '0')}
      </span>
    </div>
  );
}

export default function MobileDaytrade() {
  const { data: live = MOCK_LIVE } = useQuery({
    queryKey: ['daytrade-live'],
    queryFn: () => api.getDaytradeLive(),
    refetchInterval: 30000,
    retry: false,
  });

  const netValue = live.net_value;
  const sparkPoints = [netValue * 0.998, netValue * 0.999, netValue * 1.001, netValue * 1.002, netValue * 1.0123];

  return (
    <div style={{ maxWidth: 402, margin: '0 auto', background: 'var(--bg)', minHeight: '100dvh', display: 'flex', flexDirection: 'column' }}>
      {/* Countdown banner */}
      <CountdownBanner seconds={live.countdown_seconds} />

      {/* PnL hero */}
      <div style={{ padding: '20px 16px 12px', background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 6 }}>日內淨損益</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 36, fontWeight: 800, color: live.net_pnl >= 0 ? 'var(--up)' : 'var(--down)' }}>
          {live.net_pnl >= 0 ? '+' : ''}{live.net_pnl.toLocaleString()}
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--muted)', marginTop: 4 }}>
          淨值 {netValue.toLocaleString()}
        </div>
      </div>

      {/* Mini net value sparkline card */}
      <div style={{ background: 'var(--surface)', margin: '8px 12px', borderRadius: 4, border: '1px solid var(--border)', padding: '10px 14px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <span style={{ fontSize: 9, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 0.5 }}>淨值走勢（日內）</span>
          <div style={{ display: 'flex', gap: 12 }}>
            <span style={{ fontSize: 10, color: 'var(--muted)' }}>未實現 <span style={{ color: live.unrealized_pnl >= 0 ? 'var(--up)' : 'var(--down)', fontFamily: 'var(--font-mono)' }}>+{live.unrealized_pnl.toLocaleString()}</span></span>
            <span style={{ fontSize: 10, color: 'var(--muted)' }}>已實現 <span style={{ color: live.realized_pnl >= 0 ? 'var(--up)' : 'var(--down)', fontFamily: 'var(--font-mono)' }}>+{live.realized_pnl.toLocaleString()}</span></span>
          </div>
        </div>
        <svg viewBox="0 0 350 56" style={{ width: '100%', height: 56 }}>
          {(() => {
            const min = Math.min(...sparkPoints), max = Math.max(...sparkPoints);
            const range = max - min || 1;
            const pts = sparkPoints.map((v, i) => `${(i / (sparkPoints.length - 1)) * 350},${56 - ((v - min) / range) * 44}`).join(' ');
            const fillPts = `${pts} 350,56 0,56`;
            return (
              <>
                <polygon points={fillPts} fill="var(--up)" fillOpacity={0.1} />
                <polyline points={pts} fill="none" stroke="var(--up)" strokeWidth={2} />
              </>
            );
          })()}
        </svg>
      </div>

      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1, background: 'var(--border)', margin: '0 0 8px' }}>
        {[
          { label: '監控中', value: live.monitoring_count },
          { label: '已平倉', value: live.closed_count },
          { label: '資金使用', value: `${(live.risk.utilization * 100).toFixed(0)}%` },
        ].map(kpi => (
          <div key={kpi.label} style={{ background: 'var(--surface)', padding: '10px 12px', textAlign: 'center' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700 }}>{kpi.value}</div>
            <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 2 }}>{kpi.label}</div>
          </div>
        ))}
      </div>

      {/* Position cards */}
      <div style={{ flex: 1, padding: '0 12px 16px' }}>
        <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 8 }}>持倉 · {live.positions.length} 檔</div>
        {live.positions.map(pos => <PositionCard key={pos.code} pos={pos} />)}
      </div>
    </div>
  );
}
