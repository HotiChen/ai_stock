// 07.1 手機 · AI 預測卡 (iPhone 402×874)
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api';
import { queryState } from '../../components/DataState';
import type { Pick } from '../../types';

function MiniSparkline({ data, color }: { data: number[]; color: string }) {
  if (data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data);
  const range = max - min || 1;
  const W = 60, H = 24;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * W},${H - ((v - min) / range) * H}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: W, height: H }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}

function MobilePickCard({ pick, rank }: { pick: Pick; rank: number }) {
  const isPos = pick.change_pct >= 0;
  const tier = pick.confidence >= 0.75 ? '高' : pick.confidence >= 0.60 ? '中' : '低';
  const tierColor = pick.confidence >= 0.75 ? 'var(--up)' : pick.confidence >= 0.60 ? '#d4a017' : 'var(--down)';
  const actionColors: Record<string, string> = { approved: 'var(--up)', pending: '#d4a017', observe: 'var(--muted)', rejected: 'var(--down)' };
  const actionLabels: Record<string, string> = { approved: '已批准', pending: '待批准', observe: '觀察', rejected: '已拒絕' };

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 4, padding: 16, marginBottom: 10 }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--muted)', minWidth: 18 }}>#{rank}</span>
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700 }}>{pick.code}</div>
            <div style={{ fontSize: 13, color: 'var(--fg)' }}>{pick.name}</div>
          </div>
          <span style={{ fontSize: 9, padding: '2px 7px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 2, color: 'var(--muted)' }}>{pick.sector}</span>
        </div>
        <span style={{ fontSize: 10, padding: '3px 8px', borderRadius: 2, background: actionColors[pick.action] + '20', color: actionColors[pick.action], border: `1px solid ${actionColors[pick.action]}`, fontFamily: 'var(--font-mono)' }}>
          {actionLabels[pick.action]}
        </span>
      </div>

      {/* Price + change */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 24, fontWeight: 800 }}>{pick.last_price.toLocaleString()}</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: isPos ? 'var(--up)' : 'var(--down)', fontWeight: 600 }}>
          {isPos ? '+' : ''}{(pick.change_pct * 100).toFixed(2)}%
        </span>
        <MiniSparkline data={pick.spark} color={isPos ? 'var(--up)' : 'var(--down)'} />
      </div>

      {/* Confidence */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>信心</span>
        <div style={{ flex: 1, height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{ height: '100%', width: `${pick.confidence * 100}%`, background: tierColor }} />
        </div>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, color: tierColor }}>
          {tier} {Math.round(pick.confidence * 100)}%
        </span>
      </div>

      {/* Reason */}
      <div style={{ background: 'var(--bg)', borderRadius: 2, padding: '8px 10px', marginBottom: 10, fontSize: 12, color: 'var(--muted)', lineHeight: 1.5 }}>
        {pick.reason}
      </div>

      {/* TP / SL */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <div style={{ background: 'rgba(200,51,43,0.08)', borderRadius: 2, padding: '6px 10px' }}>
          <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 2, fontFamily: 'var(--font-mono)' }}>TARGET</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, color: 'var(--up)' }}>{pick.target_price.toLocaleString()}</div>
        </div>
        <div style={{ background: 'rgba(46,125,79,0.08)', borderRadius: 2, padding: '6px 10px' }}>
          <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 2, fontFamily: 'var(--font-mono)' }}>STOP LOSS</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, color: 'var(--down)' }}>{pick.stop_loss_price.toLocaleString()}</div>
        </div>
      </div>

      {/* Tags */}
      <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
        {pick.tags.map(tag => (
          <span key={tag} style={{ fontSize: 9, padding: '2px 7px', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 2, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>{tag}</span>
        ))}
      </div>
    </div>
  );
}

export default function MobilePredict() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['topN'],
    queryFn: () => api.getTopN(),
    retry: false,
  });

  const state = queryState({
    isLoading, isError, error,
    isEmpty: !data || data.picks.length === 0,
    what: '今日選股',
    emptyDetail: '08:30 盤前選股完成後才會有候選。',
  });
  if (state) {
    return (
      <div style={{ maxWidth: 402, margin: '0 auto', background: 'var(--bg)', minHeight: '100dvh', padding: 16 }}>
        {state}
      </div>
    );
  }
  const run = data!;

  const nowMsToOpen = (() => {
    const now = new Date();
    const open = new Date(now);
    open.setHours(9, 0, 0, 0);
    const diff = Math.max(0, Math.floor((open.getTime() - now.getTime()) / 1000));
    if (diff === 0) return '已開盤';
    const h = Math.floor(diff / 3600), m = Math.floor((diff % 3600) / 60), s = diff % 60;
    return h > 0 ? `${h}h ${m}m` : `${m}m ${s}s`;
  })();

  return (
    <div style={{ maxWidth: 402, margin: '0 auto', background: 'var(--bg)', minHeight: '100dvh', display: 'flex', flexDirection: 'column', position: 'relative' }}>
      {/* Mobile nav row */}
      <div style={{ padding: '12px 16px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700, letterSpacing: 1 }}>AI · PREMARKET</span>
        </div>
        <span style={{ fontSize: 9, padding: '2px 8px', background: 'var(--accent)', color: '#fff', borderRadius: 10, fontFamily: 'var(--font-mono)' }}>
          {run.telegram_sent_at ? new Date(run.telegram_sent_at).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' }) : '--:--'}
        </span>
      </div>

      {/* Hero */}
      <div style={{ padding: '16px 16px 12px' }}>
        <div style={{ fontSize: 22, fontWeight: 800, marginBottom: 4 }}>今日推薦</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--muted)' }}>{run.date} · {run.picks.length} 檔</div>
      </div>

      {/* Mini KPI tiles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1, background: 'var(--border)', margin: '0 0 12px' }}>
        {[
          { label: '掃描', value: run.scanned },
          { label: '買進信號', value: run.buy_signals },
          { label: '已批准', value: run.approved },
        ].map(kpi => (
          <div key={kpi.label} style={{ background: 'var(--surface)', padding: '10px 12px', textAlign: 'center' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 700 }}>{kpi.value}</div>
            <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 2 }}>{kpi.label}</div>
          </div>
        ))}
      </div>

      {/* Pick cards */}
      <div style={{ flex: 1, padding: '0 12px 80px' }}>
        {run.picks.map((pick, i) => <MobilePickCard key={pick.code} pick={pick} rank={i + 1} />)}
      </div>

      {/* Bottom bar: countdown to open */}
      <div style={{ position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)', width: '100%', maxWidth: 402, background: '#0d1117', borderTop: '1px solid #333', padding: '12px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 100 }}>
        <span style={{ fontSize: 11, color: '#666', fontFamily: 'var(--font-mono)', letterSpacing: 0.5 }}>距 09:00 開盤</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 700, color: '#ccc' }}>{nowMsToOpen}</span>
      </div>
    </div>
  );
}
