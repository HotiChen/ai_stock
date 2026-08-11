// 03.2 AI 預測 · 個股深度 — Wave 3-G
import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import AppChrome from '../../components/AppChrome';
import Pill from '../../components/Pill';
import Card from '../../components/Card';
import Eyebrow from '../../components/Eyebrow';
import Button from '../../components/Button';
import ConfidenceBar from '../../components/ConfidenceBar';
import EmptyHint from '../../components/EmptyHint';
import { api, isMockData } from '../../api';
import type { DeepAnalysis as DeepAnalysisType, Indicator, Scenario } from '../../types';

// 本頁一律使用真實 API 資料，不再有 mockDeepAnalysis() 靜默頂替。
// 沒有資料（或後端回傳示範資料）一律顯示 EmptyHint，見下方 useEffect 與 render 邏輯。

// ── Indicator row ──────────────────────────────────────────────────────────────
function IndicatorRow({ ind, last }: { ind: Indicator; last: boolean }) {
  const sigColor = ind.signal === 'bull' ? 'var(--up)' : ind.signal === 'bear' ? 'var(--down)' : 'var(--muted)';
  const sigBg = ind.signal === 'bull' ? 'var(--up-soft)' : ind.signal === 'bear' ? 'var(--down-soft)' : 'var(--surface-2)';
  const sigBorder = ind.signal === 'bull' ? 'var(--up)' : ind.signal === 'bear' ? 'var(--down)' : 'var(--hair)';
  const sigLabel = ind.signal === 'bull' ? '多' : ind.signal === 'bear' ? '空' : '中';

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '100px 1fr 1fr 60px',
      alignItems: 'center', gap: 8, height: 38,
      borderBottom: last ? 'none' : '1px solid var(--hair)',
      padding: '0 14px',
    }}>
      <div style={{ fontSize: 11, color: 'var(--ink-2)', fontWeight: 500 }}>{ind.key}</div>
      <div style={{
        fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
        fontSize: 12, color: 'var(--ink)', fontWeight: 600,
      }}>
        {typeof ind.value === 'number' ? ind.value.toFixed(1) : ind.value}
      </div>
      <div style={{ fontSize: 10, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {ind.hint}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
        <Pill color={sigColor} bg={sigBg} border={sigBorder} size={10}>{sigLabel}</Pill>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--muted)' }}>×{ind.weight}</span>
      </div>
    </div>
  );
}

// ── Scenario card ──────────────────────────────────────────────────────────────
function ScenarioCard({ scenario }: { scenario: Scenario }) {
  const isPositive = scenario.return_pct >= 0;
  return (
    <div style={{ border: '1px solid var(--hair)', marginBottom: 8 }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '8px 12px', borderBottom: '1px solid var(--hair)',
        background: 'var(--surface-2)',
      }}>
        <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--ink)' }}>{scenario.name}</span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
            fontSize: 12, color: isPositive ? 'var(--up)' : 'var(--down)', fontWeight: 600,
          }}>
            {isPositive ? '+' : ''}{scenario.return_pct.toFixed(2)}%
          </span>
        </div>
      </div>
      {/* Probability bar */}
      <div style={{ padding: '8px 12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, fontSize: 10 }}>
          <span style={{ color: 'var(--muted)' }}>機率</span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
            color: 'var(--ink)', fontWeight: 600,
          }}>
            {(scenario.probability * 100).toFixed(0)}%
          </span>
        </div>
        <div style={{ height: 4, background: 'var(--hair)', borderRadius: 1, overflow: 'hidden' }}>
          <div style={{
            width: `${scenario.probability * 100}%`, height: '100%',
            background: isPositive ? 'var(--up)' : 'var(--down)',
          }} />
        </div>
        <div style={{ marginTop: 6, fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
          {scenario.description}
        </div>
      </div>
    </div>
  );
}

// ── Price SVG chart ────────────────────────────────────────────────────────────
function PriceChart({ data }: { data: DeepAnalysisType }) {
  const closes = data.ticks.map((t) => t.close);
  const allPrices = [
    ...data.ticks.flatMap((t) => [t.high, t.low]),
    data.target_price,
    data.stop_loss_price,
  ];
  const minP = Math.min(...allPrices) * 0.998;
  const maxP = Math.max(...allPrices) * 1.002;
  const range = maxP - minP || 1;

  const W = 340, H = 160;
  const PAD_L = 0, PAD_R = 8, PAD_T = 8, PAD_B = 20;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const n = closes.length;

  const toX = (i: number) => PAD_L + (i / (n - 1)) * innerW;
  const toY = (v: number) => PAD_T + (1 - (v - minP) / range) * innerH;

  const linePoints = closes.map((c, i) => `${toX(i).toFixed(1)},${toY(c).toFixed(1)}`).join(' ');
  const tpY = toY(data.target_price);
  const slY = toY(data.stop_loss_price);
  const lastY = toY(closes[closes.length - 1]);
  const ma20 = closes.reduce((s, _c, i, arr) => {
    if (i < 19) return [...s, null];
    const slice = arr.slice(i - 19, i + 1);
    return [...s, slice.reduce((a, b) => a + b, 0) / 20];
  }, [] as (number | null)[]);
  const maPoints = ma20
    .map((v, i) => v !== null ? `${toX(i).toFixed(1)},${toY(v!).toFixed(1)}` : null)
    .filter(Boolean).join(' ');

  return (
    <svg width={W} height={H} style={{ display: 'block', overflow: 'visible' }}>
      {/* TP zone */}
      <rect x={PAD_L} y={tpY} width={innerW} height={Math.max(0, lastY - tpY)}
        fill="var(--up)" opacity={0.05} />
      {/* SL zone */}
      <rect x={PAD_L} y={lastY} width={innerW} height={Math.max(0, slY - lastY)}
        fill="var(--down)" opacity={0.05} />

      {/* Price line */}
      <polyline points={linePoints} fill="none" stroke="var(--ink)" strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" />

      {/* MA20 */}
      {maPoints && (
        <polyline points={maPoints} fill="none" stroke="var(--gold)" strokeWidth={1}
          strokeLinejoin="round" strokeDasharray="4 2" />
      )}

      {/* TP line */}
      <line x1={PAD_L} y1={tpY} x2={W - PAD_R} y2={tpY}
        stroke="var(--up)" strokeWidth={1} strokeDasharray="5 3" />
      <text x={W - PAD_R + 2} y={tpY + 4} fontSize={9} fill="var(--up)" fontFamily="var(--font-mono)">
        TP
      </text>

      {/* SL line */}
      <line x1={PAD_L} y1={slY} x2={W - PAD_R} y2={slY}
        stroke="var(--down)" strokeWidth={1} strokeDasharray="5 3" />
      <text x={W - PAD_R + 2} y={slY + 4} fontSize={9} fill="var(--down)" fontFamily="var(--font-mono)">
        SL
      </text>

      {/* Current price dot */}
      <circle cx={toX(n - 1)} cy={lastY} r={3} fill="var(--up)" />

      {/* X axis dates */}
      {[0, Math.floor(n / 2), n - 1].map((i) => (
        <text key={i} x={toX(i)} y={H - 4} textAnchor="middle"
          fontSize={8} fill="var(--muted)" fontFamily="var(--font-mono)">
          {data.ticks[i]?.t ?? ''}
        </text>
      ))}
    </svg>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function DeepAnalysis() {
  const { code } = useParams<{ code: string }>();
  const navigate = useNavigate();
  const [data, setData] = useState<DeepAnalysisType | null>(null);
  const [loading, setLoading] = useState(true);
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState(false);

  useEffect(() => {
    if (!code) return;
    api.getDeepAnalysis(code)
      .then((d) => setData(isMockData(d) ? null : d))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [code]);

  function handleApprove() {
    if (!data) return;
    setApproving(true);
    api.approvePick('', data.code)
      .then(() => setApproved(true))
      .catch(() => setApproved(true)) // optimistic
      .finally(() => setApproving(false));
  }

  function handleReject() {
    if (!data) return;
    api.rejectPick('', data.code).catch(() => { /* ignore */ });
    navigate('/predict');
  }

  if (loading) {
    return (
      <AppChrome title={`深度分析 · ${code}`} eyebrow="03.2">
        <div style={{ padding: 20, color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>載入中…</div>
      </AppChrome>
    );
  }

  if (!data) {
    return (
      <AppChrome title={`深度分析 · ${code}`} eyebrow="03.2">
        <EmptyHint text="尚無深度分析資料，請確認代碼或稍後再試" />
      </AppChrome>
    );
  }

  const d = data;
  const totalScore = d.indicators.reduce((sum, ind) => {
    if (ind.signal === 'bull') return sum + ind.weight;
    if (ind.signal === 'bear') return sum - ind.weight;
    return sum;
  }, 0);

  return (
    <AppChrome title={`${d.code} · ${d.name} · 深度分析`} eyebrow="03.2">
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>

        {/* ── Identity strip ───────────────────────────────────── */}
        <div style={{
          background: 'var(--surface)', borderBottom: '1px solid var(--hair)',
          padding: '12px 20px', display: 'flex', gap: 32, flexShrink: 0, flexWrap: 'wrap',
        }}>
          {/* Left: code + name + price */}
          <div style={{ width: 280, flexShrink: 0 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 4 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1', fontSize: 18, fontWeight: 600, color: 'var(--ink)' }}>
                {d.code}
              </span>
              <span style={{ fontSize: 14, color: 'var(--ink-2)', fontWeight: 500 }}>{d.name}</span>
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>{d.sector}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 6 }}>
              <span style={{
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 28, fontWeight: 600, color: 'var(--ink)',
              }}>
                {d.last.toLocaleString('zh-TW')}
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 14, color: d.change_pct >= 0 ? 'var(--up)' : 'var(--down)', fontWeight: 500,
              }}>
                {d.change_pct >= 0 ? '+' : ''}{d.change_pct.toFixed(2)}%
              </span>
            </div>
            {/* OHLV */}
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(5, auto)', gap: '2px 12px',
              fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1', fontSize: 10,
            }}>
              {[
                { k: 'O', v: d.open },
                { k: 'H', v: d.high },
                { k: 'L', v: d.low },
                { k: 'PC', v: d.prev_close },
                { k: 'VOL', v: d.volume_lots.toLocaleString('zh-TW') + '張' },
              ].map((item) => (
                <div key={item.k} style={{ display: 'flex', gap: 4 }}>
                  <span style={{ color: 'var(--muted)' }}>{item.k}</span>
                  <span style={{ color: 'var(--ink-2)' }}>{item.v}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Divider */}
          <div style={{ width: 1, background: 'var(--hair)', flexShrink: 0, alignSelf: 'stretch' }} />

          {/* Right: AI verdict */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <Eyebrow label="AI 判決" />
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              {d.signal === 'buy' ? (
                <Pill color="var(--up)" bg="var(--up-soft)" border="var(--up)">BUY</Pill>
              ) : d.signal === 'sell' ? (
                <Pill color="var(--down)" bg="var(--down-soft)" border="var(--down)">SELL</Pill>
              ) : (
                <Pill color="var(--muted)" bg="var(--surface-2)" border="var(--hair)">HOLD</Pill>
              )}
              <span style={{
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 22, fontWeight: 600,
                color: d.confidence >= 0.75 ? 'var(--up)' : d.confidence >= 0.60 ? 'var(--gold)' : 'var(--muted)',
              }}>
                {(d.confidence * 100).toFixed(0)}%
              </span>
              <ConfidenceBar value={d.confidence} label={false} />
            </div>
            {/* TP / SL / RR / Budget */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
              {[
                { label: 'TP', value: d.target_price.toLocaleString('zh-TW'), color: 'var(--up)' },
                { label: 'SL', value: d.stop_loss_price.toLocaleString('zh-TW'), color: 'var(--down)' },
                { label: 'R/R', value: d.risk_reward, color: 'var(--ink)' },
                { label: 'Budget', value: 'NT$' + (d.budget / 1000).toFixed(0) + 'k', color: 'var(--gold)' },
              ].map((item) => (
                <div key={item.label} style={{
                  background: 'var(--surface-2)', border: '1px solid var(--hair)',
                  padding: '6px 10px',
                }}>
                  <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 2 }}>
                    {item.label}
                  </div>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                    fontSize: 13, fontWeight: 600, color: item.color,
                  }}>
                    {item.value}
                  </div>
                </div>
              ))}
            </div>
            {/* Action buttons */}
            <div style={{ display: 'flex', gap: 8 }}>
              <Button
                variant={approved ? 'default' : 'primary'}
                onClick={handleApprove}
                disabled={approving || approved}
              >
                {approved ? '已批准' : approving ? '批准中…' : '批准 · 加入今日清單'}
              </Button>
              <Button onClick={handleReject}>跳過</Button>
              <Button variant="ghost" onClick={() => navigate('/predict/' + d.code + '/reasoning')}>
                查看推理過程 →
              </Button>
            </div>
          </div>
        </div>

        {/* ── Main 3-column body ───────────────────────────────── */}
        <div style={{
          flex: 1, overflow: 'auto',
          display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr',
          gap: 12, padding: 16,
          alignItems: 'start',
        }}>

          {/* LEFT: Price chart */}
          <Card label="20 日價格走勢" right={
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--gold)' }}>
              — MA20
            </span>
          } padding={0}>
            <div style={{ padding: '12px 12px 0' }}>
              <PriceChart data={d} />
            </div>
            {/* Sub-stats */}
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr 1fr',
              borderTop: '1px solid var(--hair)', gap: 0,
            }}>
              {[
                { label: '預期報酬', value: '+' + d.expected_return.toFixed(2) + '%', color: 'var(--up)' },
                { label: '最大下行', value: d.max_loss.toFixed(2) + '%', color: 'var(--down)' },
                { label: '建議部位', value: 'NT$' + (d.budget / 1000).toFixed(0) + 'k', color: 'var(--ink)' },
                { label: '進場方式', value: '分兩批', color: 'var(--gold)' },
              ].map((item, i) => (
                <div key={item.label} style={{
                  padding: '8px 12px',
                  borderBottom: i < 2 ? '1px solid var(--hair)' : 'none',
                  borderRight: i % 2 === 0 ? '1px solid var(--hair)' : 'none',
                }}>
                  <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 2 }}>{item.label}</div>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                    fontSize: 13, fontWeight: 600, color: item.color,
                  }}>
                    {item.value}
                  </div>
                </div>
              ))}
            </div>
          </Card>

          {/* MIDDLE: Technical indicators */}
          <Card label="技術指標" right={
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--muted)' }}>
              {d.indicators.length} 項
            </span>
          } padding={0}>
            {d.indicators.map((ind, i) => (
              <IndicatorRow key={ind.key} ind={ind} last={i === d.indicators.length - 1} />
            ))}
            {/* Score footer */}
            <div style={{
              borderTop: '2px solid var(--hair)', padding: '10px 14px',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              background: 'var(--surface-2)',
            }}>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>總分</div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                  fontSize: 22, fontWeight: 600, color: 'var(--up)',
                }}>
                  +{totalScore.toFixed(1)}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em', textAlign: 'right' }}>推薦</div>
                <Pill color="var(--up)" bg="var(--up-soft)" border="var(--up)">{d.recommendation}</Pill>
              </div>
            </div>
          </Card>

          {/* RIGHT: Scenarios + AI conclusion */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Card label="情境分析" padding={12}>
              {d.scenarios.map((s) => (
                <ScenarioCard key={s.name} scenario={s} />
              ))}
            </Card>

            {/* AI conclusion */}
            <div style={{
              background: 'var(--gold-soft)', border: '1px solid var(--gold)',
              padding: 14,
            }}>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--gold)',
                letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 8,
              }}>
                AI 結論
              </div>
              <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.7, marginBottom: 10 }}>
                {d.ai_conclusion}
              </div>
              <div style={{
                display: 'flex', justifyContent: 'space-between',
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 10, color: 'var(--muted)',
              }}>
                <span>{d.model}</span>
                <span>{new Date(d.generated_at).toLocaleTimeString('zh-TW')}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </AppChrome>
  );
}
