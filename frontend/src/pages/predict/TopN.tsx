// 03.1 AI 預測 · Top N 候選股 — Wave 3-G
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AppChrome from '../../components/AppChrome';
import Pill from '../../components/Pill';
import ConfidenceBar from '../../components/ConfidenceBar';
import Sparkline from '../../components/Sparkline';
import Button from '../../components/Button';
import Card from '../../components/Card';
import { api } from '../../api';
import type { TopNRun, Pick, RiskCheck, SectorAllocation } from '../../types';

// ── Mock data ──────────────────────────────────────────────────────────────────
const MOCK_RUN: TopNRun = {
  run_id: 'PRE-20260525-001',
  date: '2026-05-25',
  scanned: 1823,
  analyzed: 47,
  buy_signals: 8,
  hold_signals: 6,
  approved: 6,
  rejected_by_risk: 2,
  picks: [
    {
      code: '2330', name: '台積電', sector: '半導體', signal: 'buy',
      confidence: 0.91, target_price: 1020, stop_loss_price: 940,
      last_price: 975, change_pct: 1.56,
      spark: [920, 935, 918, 945, 960, 952, 968, 975, 980, 975],
      reason: '法人連買5日，突破前高壓力，RSI回測支撐',
      tags: ['法人', '突破', 'RSI支撐'],
      action: 'approved', budget: 142000, budget_ratio: 0.24,
      run_id: 'PRE-20260525-001', created_at: '2026-05-25T08:30:00Z',
    },
    {
      code: '2454', name: '聯發科', sector: 'IC設計', signal: 'buy',
      confidence: 0.87, target_price: 1350, stop_loss_price: 1210,
      last_price: 1265, change_pct: 2.10,
      spark: [1180, 1200, 1195, 1220, 1240, 1230, 1255, 1265, 1270, 1265],
      reason: 'AI晶片需求爆發，本季EPS上修，MACD金叉',
      tags: ['EPS上修', 'MACD金叉', 'AI概念'],
      action: 'approved', budget: 126000, budget_ratio: 0.21,
      run_id: 'PRE-20260525-001', created_at: '2026-05-25T08:30:00Z',
    },
    {
      code: '2382', name: '廣達', sector: '伺服器', signal: 'buy',
      confidence: 0.83, target_price: 255, stop_loss_price: 218,
      last_price: 235, change_pct: 0.85,
      spark: [210, 215, 212, 220, 228, 225, 232, 235, 238, 235],
      reason: 'AI伺服器出貨量創新高，外資持續回補',
      tags: ['伺服器', '外資回補'],
      action: 'pending', budget: 94000, budget_ratio: 0.16,
      run_id: 'PRE-20260525-001', created_at: '2026-05-25T08:30:00Z',
    },
    {
      code: '2317', name: '鴻海', sector: '電子製造', signal: 'buy',
      confidence: 0.78, target_price: 215, stop_loss_price: 192,
      last_price: 202, change_pct: -0.49,
      spark: [198, 205, 200, 208, 210, 205, 203, 200, 202, 202],
      reason: '電動車訂單增溫，股價修正後具低接價值',
      tags: ['電動車', '低接'],
      action: 'pending', budget: 80800, budget_ratio: 0.14,
      run_id: 'PRE-20260525-001', created_at: '2026-05-25T08:30:00Z',
    },
    {
      code: '3711', name: '日月光投控', sector: '封裝測試', signal: 'buy',
      confidence: 0.76, target_price: 142, stop_loss_price: 128,
      last_price: 134, change_pct: 1.20,
      spark: [125, 128, 127, 130, 132, 131, 133, 134, 135, 134],
      reason: 'CoWoS封裝需求強勁，毛利率持續改善',
      tags: ['CoWoS', '毛利改善'],
      action: 'pending', budget: 53600, budget_ratio: 0.09,
      run_id: 'PRE-20260525-001', created_at: '2026-05-25T08:30:00Z',
    },
    {
      code: '2357', name: '華碩', sector: '電腦周邊', signal: 'hold',
      confidence: 0.62, target_price: 560, stop_loss_price: 500,
      last_price: 528, change_pct: -0.75,
      spark: [540, 535, 530, 525, 520, 518, 522, 528, 525, 528],
      reason: '筆電出貨增溫但競爭激烈，觀望為宜',
      tags: ['筆電', '觀望'],
      action: 'observe', budget: 52800, budget_ratio: 0.09,
      run_id: 'PRE-20260525-001', created_at: '2026-05-25T08:30:00Z',
    },
    {
      code: '2303', name: '聯電', sector: '半導體', signal: 'hold',
      confidence: 0.58, target_price: 52, stop_loss_price: 46,
      last_price: 49, change_pct: -0.20,
      spark: [50, 49, 48, 49, 50, 49, 48, 49, 49, 49],
      reason: '成熟製程需求趨緩，暫不追高',
      tags: ['成熟製程', '趨緩'],
      action: 'observe', budget: 0, budget_ratio: 0,
      run_id: 'PRE-20260525-001', created_at: '2026-05-25T08:30:00Z',
    },
    {
      code: '2412', name: '中華電', sector: '電信', signal: 'hold',
      confidence: 0.55, target_price: 120, stop_loss_price: 109,
      last_price: 114, change_pct: 0.09,
      spark: [113, 114, 113, 114, 115, 114, 114, 113, 114, 114],
      reason: '防禦性部位，5G進展緩慢',
      tags: ['防禦', '5G'],
      action: 'observe', budget: 0, budget_ratio: 0,
      run_id: 'PRE-20260525-001', created_at: '2026-05-25T08:30:00Z',
    },
  ],
  risk_checks: [
    { key: '板塊集中度', sub: '半導體 38%', status: 'warn', detail: '半導體合計38%，接近40%上限' },
    { key: '單筆部位', sub: '≤ 25%', status: 'pass', detail: '所有持倉均在25%以下' },
    { key: '日內虧損上限', sub: '< -3%', status: 'pass', detail: '累計損益 +0.74%' },
    { key: 'AI信心門檻', sub: '≥ 0.75', status: 'pass', detail: '6/8 檔超過門檻' },
    { key: '黑名單過濾', sub: '0 衝突', status: 'pass', detail: '無黑名單標的' },
    { key: 'Telegram 確認', sub: '已送出', status: 'pass', detail: '08:32:14 已送達' },
  ],
  sector_allocation: [
    { name: '半導體', ratio: 0.38, limit: 0.40, value: 228000 },
    { name: 'IC設計', ratio: 0.21, limit: 0.30, value: 126000 },
    { name: '伺服器', ratio: 0.16, limit: 0.30, value: 96000 },
    { name: '電子製造', ratio: 0.14, limit: 0.25, value: 84000 },
    { name: '封裝測試', ratio: 0.09, limit: 0.20, value: 54000 },
  ],
  blacklist: ['2890', '2881'],
  cost: {
    duration_ms: 18240,
    input_tokens: 12480,
    output_tokens: 3840,
    cost_usd: 0.0846,
    model: 'claude-sonnet-4-6',
  },
  telegram_sent_at: '2026-05-25T08:32:14Z',
};

// ── Sub-tab ────────────────────────────────────────────────────────────────────
const TABS = ['今日推薦', '深度分析', '推理過程', '多模型比較', '預測 vs 實際'];

// ── Helper functions ───────────────────────────────────────────────────────────
function fmtPrice(n: number): string {
  return n.toLocaleString('zh-TW');
}

function fmtChangePct(v: number): string {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
}

function signalPill(sig: string) {
  if (sig === 'buy') return (
    <Pill color="var(--up)" bg="var(--up-soft)" border="var(--up)" size={10}>BUY</Pill>
  );
  if (sig === 'sell') return (
    <Pill color="var(--down)" bg="var(--down-soft)" border="var(--down)" size={10}>SELL</Pill>
  );
  return (
    <Pill color="var(--muted)" bg="var(--surface-2)" border="var(--hair)" size={10}>HOLD</Pill>
  );
}

function actionPill(action: string) {
  switch (action) {
    case 'approved':
      return <Pill color="var(--down)" bg="var(--down-soft)" border="var(--down)" size={10}>已批准</Pill>;
    case 'pending':
      return <Pill color="var(--gold)" bg="var(--gold-soft)" border="var(--gold)" size={10}>待確認</Pill>;
    case 'observe':
      return <Pill color="var(--muted)" bg="var(--surface-2)" border="var(--hair)" size={10}>觀望</Pill>;
    case 'rejected':
      return <Pill color="var(--up)" bg="var(--up-soft)" border="var(--up)" size={10}>已跳過</Pill>;
    default:
      return null;
  }
}

// ── Distribution histogram (SVG) ───────────────────────────────────────────────
function DistHistogram() {
  const bins = [
    { label: '-5%', v: 2 }, { label: '-3%', v: 5 }, { label: '-1%', v: 9 },
    { label: '+1%', v: 18 }, { label: '+3%', v: 28 }, { label: '+5%', v: 20 },
    { label: '+7%', v: 10 },
  ];
  const maxV = Math.max(...bins.map((b) => b.v));
  const W = 220, H = 80, bw = 26, gap = 5;

  return (
    <svg width={W} height={H + 20} aria-label="預期結果分佈">
      {bins.map((bin, i) => {
        const bh = (bin.v / maxV) * H;
        const x = i * (bw + gap);
        const isMedian = i === 4;
        return (
          <g key={bin.label}>
            <rect
              x={x} y={H - bh} width={bw} height={bh}
              fill={bin.label.startsWith('+') ? 'var(--up-soft)' : 'var(--down-soft)'}
              stroke={isMedian ? 'var(--up)' : 'none'}
              strokeWidth={isMedian ? 1.5 : 0}
            />
            <text x={x + bw / 2} y={H + 14} textAnchor="middle"
              fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">
              {bin.label}
            </text>
          </g>
        );
      })}
      {/* median line */}
      <line x1={4 * (bw + gap) + bw / 2} y1={0} x2={4 * (bw + gap) + bw / 2} y2={H}
        stroke="var(--up)" strokeWidth={1.5} strokeDasharray="3 2" />
    </svg>
  );
}

// ── Risk checks card ───────────────────────────────────────────────────────────
function RiskChecksCard({ checks }: { checks: RiskCheck[] }) {
  return (
    <Card label="風控紀要" padding={0}>
      {checks.map((c, i) => (
        <div key={i} style={{
          display: 'flex', alignItems: 'flex-start', gap: 10,
          padding: '8px 14px', borderBottom: i < checks.length - 1 ? '1px solid var(--hair)' : 'none',
        }}>
          <span style={{
            flexShrink: 0, fontSize: 12, marginTop: 1,
            color: c.status === 'pass' ? 'var(--down)' : c.status === 'warn' ? 'var(--gold)' : 'var(--up)',
          }}>
            {c.status === 'pass' ? '✓' : c.status === 'warn' ? '!' : '✗'}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12, fontWeight: 500, color: 'var(--ink)' }}>{c.key}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 1 }}>{c.sub} · {c.detail}</div>
          </div>
        </div>
      ))}
    </Card>
  );
}

// ── Sector concentration card ──────────────────────────────────────────────────
function SectorCard({ alloc }: { alloc: SectorAllocation[] }) {
  return (
    <Card label="板塊集中度" padding={12}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {alloc.map((s) => {
          const over = s.ratio >= s.limit;
          const fillPct = Math.min(s.ratio / s.limit, 1) * 100;
          return (
            <div key={s.name}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3, fontSize: 11 }}>
                <span style={{ color: 'var(--ink-2)' }}>{s.name}</span>
                <span style={{
                  fontFamily: 'var(--font-mono)',
                  fontFeatureSettings: '"tnum" 1, "zero" 1',
                  color: over ? 'var(--up)' : 'var(--muted)',
                  fontSize: 10,
                }}>
                  {(s.ratio * 100).toFixed(0)}% / {(s.limit * 100).toFixed(0)}%
                </span>
              </div>
              <div style={{ height: 4, background: 'var(--hair)', borderRadius: 1, overflow: 'hidden' }}>
                <div style={{
                  height: '100%', width: `${fillPct}%`,
                  background: over ? 'var(--up)' : 'var(--down)',
                  transition: 'width 0.3s',
                }} />
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

// ── Table row ──────────────────────────────────────────────────────────────────
function PickRow({ pick, rank, onNavigate }: { pick: Pick; rank: number; onNavigate: (code: string) => void }) {
  const isHold = pick.signal === 'hold';
  return (
    <div
      onClick={() => onNavigate(pick.code)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onNavigate(pick.code)}
      style={{
        display: 'grid',
        gridTemplateColumns: '40px 1.2fr 0.8fr 0.6fr 1fr 1.1fr 1.1fr 1.4fr 1.6fr 0.9fr 0.8fr',
        alignItems: 'center',
        height: 44,
        borderBottom: '1px solid var(--hair)',
        background: rank % 2 === 0 ? 'var(--surface-2)' : 'var(--surface)',
        opacity: isHold ? 0.55 : 1,
        cursor: 'pointer',
        userSelect: 'none',
        padding: '0 4px',
        gap: 4,
      }}
    >
      {/* # rank */}
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1, "zero" 1',
        fontSize: 11, color: 'var(--muted)', textAlign: 'center',
      }}>
        {String(rank).padStart(2, '0')}
      </div>

      {/* 標的 + sparkline */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)', lineHeight: 1.2 }}>{pick.code}</div>
          <div style={{ fontSize: 10, color: 'var(--muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{pick.name}</div>
        </div>
        <Sparkline data={pick.spark} width={48} height={20} />
      </div>

      {/* 板塊 */}
      <div style={{ fontSize: 11, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {pick.sector}
      </div>

      {/* 訊號 */}
      <div>{signalPill(pick.signal)}</div>

      {/* AI 信心 */}
      <div><ConfidenceBar value={pick.confidence} label /></div>

      {/* 現價 / 漲幅 */}
      <div style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1', fontSize: 11 }}>
        <div style={{ color: 'var(--ink)', fontWeight: 500 }}>{fmtPrice(pick.last_price)}</div>
        <div style={{ color: pick.change_pct >= 0 ? 'var(--up)' : 'var(--down)', fontSize: 10 }}>
          {fmtChangePct(pick.change_pct)}
        </div>
      </div>

      {/* TP / SL */}
      <div style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1', fontSize: 10 }}>
        <div style={{ color: 'var(--up)' }}>▲ {fmtPrice(pick.target_price)}</div>
        <div style={{ color: 'var(--down)' }}>▼ {fmtPrice(pick.stop_loss_price)}</div>
      </div>

      {/* 進場理由 */}
      <div style={{ fontSize: 11, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {pick.reason}
      </div>

      {/* 技術訊號 tags */}
      <div style={{ display: 'flex', gap: 3, flexWrap: 'nowrap', overflow: 'hidden' }}>
        {pick.tags.slice(0, 2).map((t) => (
          <Pill key={t} size={10} color="var(--muted)" border="var(--hair)">{t}</Pill>
        ))}
      </div>

      {/* 建議部位 */}
      <div style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1', fontSize: 10 }}>
        {pick.budget > 0 ? (
          <>
            <div style={{ color: 'var(--ink)', fontWeight: 500 }}>NT${(pick.budget / 1000).toFixed(0)}k</div>
            <div style={{ color: 'var(--muted)' }}>{(pick.budget_ratio * 100).toFixed(0)}%</div>
          </>
        ) : (
          <span style={{ color: 'var(--muted-2)' }}>—</span>
        )}
      </div>

      {/* 狀態 */}
      <div>{actionPill(pick.action)}</div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function TopN() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState(0);
  const [run, setRun] = useState<TopNRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [rerunning, setRerunning] = useState(false);
  const [showRerunConfirm, setShowRerunConfirm] = useState(false);

  useEffect(() => {
    api.getTopN()
      .then(setRun)
      .catch(() => setRun(MOCK_RUN))
      .finally(() => setLoading(false));
  }, []);

  function handleApproveAll() {
    if (!run) return;
    api.approveRun(run.run_id)
      .then(() => {
        setRun((r) => r ? {
          ...r,
          picks: r.picks.map((p) =>
            p.signal === 'buy' ? { ...p, action: 'approved' as const } : p
          ),
        } : r);
      })
      .catch(() => { /* ignore */ });
  }

  function handleRerun() {
    setShowRerunConfirm(false);
    setRerunning(true);
    api.runPremarket()
      .then(setRun)
      .catch(() => { /* ignore */ })
      .finally(() => setRerunning(false));
  }

  if (loading) {
    return (
      <AppChrome title="AI 預測 · Top N" eyebrow="03.1">
        <div style={{ padding: 20, color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>
          載入中…
        </div>
      </AppChrome>
    );
  }

  const data = run ?? MOCK_RUN;
  const buyCount = data.picks.filter((p) => p.signal === 'buy').length;

  return (
    <AppChrome title="AI 預測 · Top N" eyebrow="03.1">
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

        {/* ── Sub-tabs ─────────────────────────────────────────── */}
        <div style={{
          height: 38, display: 'flex', alignItems: 'flex-end',
          borderBottom: '1px solid var(--hair)',
          background: 'var(--surface)', flexShrink: 0,
          padding: '0 16px', gap: 0,
        }}>
          {TABS.map((tab, i) => (
            <button
              key={tab}
              onClick={() => setActiveTab(i)}
              style={{
                height: 38, padding: '0 16px',
                background: 'transparent', border: 'none',
                borderBottom: activeTab === i ? '2px solid var(--ink)' : '2px solid transparent',
                color: activeTab === i ? 'var(--ink)' : 'var(--muted)',
                fontSize: 12, fontWeight: activeTab === i ? 500 : 400,
                cursor: 'pointer', fontFamily: 'inherit',
                whiteSpace: 'nowrap',
              }}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* ── Run Banner ───────────────────────────────────────── */}
        <div style={{
          background: 'var(--surface-2)', borderBottom: '1px solid var(--hair)',
          padding: '10px 16px', display: 'flex', alignItems: 'center',
          gap: 16, flexShrink: 0, flexWrap: 'wrap',
        }}>
          {/* RUN-ID */}
          <div style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1', fontSize: 11 }}>
            <span style={{ color: 'var(--muted)', letterSpacing: '0.1em', textTransform: 'uppercase', marginRight: 6 }}>RUN</span>
            <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{data.run_id}</span>
          </div>

          <div style={{ width: 1, height: 18, background: 'var(--hair)', flexShrink: 0 }} />

          {/* Stats */}
          {[
            { label: '掃描', value: data.scanned },
            { label: '深度', value: data.analyzed },
            { label: 'BUY', value: data.buy_signals, color: 'var(--up)' },
            { label: 'HOLD', value: data.hold_signals, color: 'var(--muted)' },
            { label: '風控過', value: data.approved, color: 'var(--down)' },
            { label: 'Telegram', value: data.telegram_sent_at ? '已送出' : '未送出',
              color: data.telegram_sent_at ? 'var(--down)' : 'var(--muted)' },
          ].map((s) => (
            <div key={s.label} style={{ display: 'flex', gap: 4, alignItems: 'baseline' }}>
              <span style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{s.label}</span>
              <span style={{
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 13, fontWeight: 600, color: (s as { color?: string }).color ?? 'var(--ink)',
              }}>
                {s.value}
              </span>
            </div>
          ))}

          <div style={{ flex: 1 }} />

          {/* Actions */}
          <div style={{ display: 'flex', gap: 8 }}>
            <Button size="small" onClick={() => setShowRerunConfirm(true)} disabled={rerunning}>
              {rerunning ? '分析中…(18s)' : '重新跑分析'}
            </Button>
            <Button size="small" variant="primary" onClick={handleApproveAll}>
              全部批准 ({buyCount})
            </Button>
          </div>
        </div>

        {/* ── Rerun confirm modal ──────────────────────────────── */}
        {showRerunConfirm && (
          <div style={{
            position: 'fixed', inset: 0, background: 'rgba(20,23,31,0.6)',
            zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <div style={{
              background: 'var(--surface)', border: '1px solid var(--hair)',
              padding: 24, width: 360,
            }}>
              <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>重新執行盤前分析</div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 20 }}>
                將重新掃描全市場並深度分析候選股，預計耗時約 18 秒，
                期間會發送新的 Telegram 確認訊息。
              </div>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                <Button size="small" onClick={() => setShowRerunConfirm(false)}>取消</Button>
                <Button size="small" variant="primary" onClick={handleRerun}>確認執行</Button>
              </div>
            </div>
          </div>
        )}

        {/* ── Table scroll area ────────────────────────────────── */}
        <div style={{ flex: 1, overflow: 'auto' }}>

          {/* Table header */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '40px 1.2fr 0.8fr 0.6fr 1fr 1.1fr 1.1fr 1.4fr 1.6fr 0.9fr 0.8fr',
            alignItems: 'center', height: 32,
            borderBottom: '2px solid var(--hair)',
            background: 'var(--surface)', position: 'sticky', top: 0, zIndex: 1,
            padding: '0 4px', gap: 4,
          }}>
            {['#', '標的', '板塊', '訊號', 'AI 信心', '現價 / 漲幅', '目標 / 停損', '進場理由', '技術訊號', '建議部位', '狀態'].map((h) => (
              <div key={h} style={{
                fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase',
                letterSpacing: '0.10em', fontFamily: 'var(--font-mono)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>{h}</div>
            ))}
          </div>

          {/* Rows */}
          {data.picks.map((pick, i) => (
            <PickRow
              key={pick.code}
              pick={pick}
              rank={i + 1}
              onNavigate={(code) => navigate('/predict/' + code)}
            />
          ))}

          {/* ── Bottom 3 cards ───────────────────────────────── */}
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
            gap: 12, padding: 16,
          }}>
            <RiskChecksCard checks={data.risk_checks} />
            <SectorCard alloc={data.sector_allocation} />
            <Card label="預期結果分佈" padding={12}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
                <DistHistogram />
                <div style={{
                  fontSize: 10, color: 'var(--muted)',
                  fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                  textAlign: 'center',
                }}>
                  中位數預期報酬 <span style={{ color: 'var(--up)', fontWeight: 600 }}>+3.2%</span>
                </div>
              </div>
            </Card>
          </div>
        </div>
      </div>
    </AppChrome>
  );
}
