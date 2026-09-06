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
import { queryState } from '../../components/DataState';
import type { TopNRun, Pick, RiskCheck, SectorAllocation } from '../../types';

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
  const [loadError, setLoadError] = useState<unknown>(null);
  const [rerunning, setRerunning] = useState(false);
  const [showRerunConfirm, setShowRerunConfirm] = useState(false);

  useEffect(() => {
    api.getTopN()
      .then(setRun)
      .catch(setLoadError)
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

  const state = queryState({
    isLoading: loading,
    isError: !!loadError,
    error: loadError,
    isEmpty: !run || run.picks.length === 0,
    what: '今日選股',
    emptyDetail: '08:30 盤前選股完成後才會有候選；也可能是今天所有候選都被過濾掉了。',
  });
  if (state) return <AppChrome title="AI 預測 · Top N" eyebrow="03.1">{state}</AppChrome>;

  const data = run!;
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
