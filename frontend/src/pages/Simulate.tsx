// 05.3 回測 / 模擬倉
import { useState, useEffect } from 'react';
import AppChrome from '../components/AppChrome';
import { api } from '../api';
import type { BacktestResult, BacktestParams } from '../types';

// ── Types ────────────────────────────────────────────────────────────────────

interface EquityPoint { date: string; capital: number }

interface PaperSummary {
  start_date: string;
  total_days: number;
  real_trades: number;
  no_trade_days: number;
  initial_capital: number;
  current_capital: number;
  total_pnl: number;
  total_pnl_pct: number;
  win_rate: number;
  hits: number;
  stops: number;
  neutrals: number;
  max_drawdown: number;
  avg_win: number;
  avg_loss: number;
  expectancy: number;
  streak_win: number;
  streak_loss: number;
  equity_curve: EquityPoint[];
}

interface PaperRecord {
  id: number;
  date: string;
  code: string | null;
  name: string | null;
  dt_score: number | null;
  entry_price: number | null;
  exit_price: number | null;
  outcome: string | null;
  pnl: number;
  pnl_pct: number;
  capital_before: number;
  capital_after: number;
  note: string;
}

// ── Backtest mock ─────────────────────────────────────────────────────────────

const MOCK_RESULT: BacktestResult = {
  id: 'BT-20260529-001',
  range: { start: '2026-01-01', end: '2026-05-29' },
  initial_capital: 1000000,
  slippage: 0.1,
  strategy: 'AI TopN v2.1',
  trades: 147,
  wins: 93,
  losses: 54,
  winrate: 0.633,
  total_pnl: 287400,
  total_pnl_pct: 0.2874,
  avg_win: 5820,
  avg_loss: -2340,
  sharpe: 1.87,
  max_dd: -0.082,
  beat_index_pct: 0.154,
  monthly_returns: [0.042, 0.071, -0.018, 0.089, 0.063, 0.031],
  equity_curve: Array.from({ length: 30 }, (_, i) => ({
    date: new Date(Date.now() - (29 - i) * 86400000).toISOString().slice(0, 10),
    me: 1000000 + i * 9870 + Math.sin(i * 0.3) * 12000,
    index: 1000000 + i * 4200 + Math.sin(i * 0.2) * 8000,
  })),
  trades_sample: [],
  ai_conclusion: '策略在五個月內表現優於大盤 15.4%，Sharpe 值 1.87 顯示風險調整後報酬良好。主要優勢來自高信心篩選（≥0.75）和板塊輪動。建議調整：\n\n1. 提高最小信心門檻至 0.78\n2. 加入大盤環境過濾（TAIEX > -0.5%）\n3. 對高 β 股放寬停損至 3.5%',
};

const STRATEGIES = ['AI TopN v2.1', 'AI TopN v2.0', 'Momentum Only', 'Mean Reversion'];

// ── Shared SVG charts ─────────────────────────────────────────────────────────

function BacktestEquitySVG({ data }: { data: { date: string; me: number; index: number }[] }) {
  if (!data.length) return null;
  const W = 600, H = 200;
  const pad = { t: 12, r: 12, b: 24, l: 60 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
  const allVals = data.flatMap(d => [d.me, d.index]);
  const minV = Math.min(...allVals), maxV = Math.max(...allVals);
  const range = maxV - minV || 1;
  const toX = (i: number) => pad.l + (i / (data.length - 1)) * iw;
  const toY = (v: number) => pad.t + ih - ((v - minV) / range) * ih;
  const mePath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(d.me).toFixed(1)}`).join(' ');
  const indexPath = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(d.index).toFixed(1)}`).join(' ');
  const fillPath = `${mePath} L${toX(data.length - 1)},${H - pad.b} L${pad.l},${H - pad.b} Z`;
  const yTicks = [minV, minV + range * 0.5, maxV];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H }}>
      <defs>
        <linearGradient id="equity-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--up)" stopOpacity="0.15" />
          <stop offset="100%" stopColor="var(--up)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {yTicks.map(v => (
        <g key={v}>
          <line x1={pad.l} y1={toY(v)} x2={W - pad.r} y2={toY(v)} stroke="var(--border)" strokeWidth={0.5} />
          <text x={pad.l - 4} y={toY(v) + 4} textAnchor="end" fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">
            {(v / 10000).toFixed(0)}萬
          </text>
        </g>
      ))}
      <path d={fillPath} fill="url(#equity-grad)" />
      <path d={indexPath} stroke="var(--muted)" strokeWidth={1} fill="none" strokeDasharray="4,3" />
      <path d={mePath} stroke="var(--up)" strokeWidth={2} fill="none" />
      {[0, Math.floor(data.length / 2), data.length - 1].map(i => (
        <text key={i} x={toX(i)} y={H - pad.b + 14} textAnchor="middle" fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">
          {data[i]?.date.slice(5)}
        </text>
      ))}
      <text x={pad.l + 8} y={pad.t + 14} fontSize={9} fill="var(--up)" fontFamily="var(--font-mono)">策略</text>
      <text x={pad.l + 40} y={pad.t + 14} fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">- - 大盤</text>
    </svg>
  );
}

function MonthlyHeatmap({ returns }: { returns: number[] }) {
  const months = ['1月', '2月', '3月', '4月', '5月', '6月'];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 2 }}>
      {returns.map((r, i) => {
        const isPos = r >= 0;
        const intensity = Math.min(Math.abs(r) / 0.1, 1);
        const bg = isPos
          ? `rgba(200, 51, 43, ${0.15 + intensity * 0.6})`
          : `rgba(46, 125, 79, ${0.15 + intensity * 0.6})`;
        return (
          <div key={i} style={{ background: bg, borderRadius: 2, padding: '10px 6px', textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 4, fontFamily: 'var(--font-mono)' }}>{months[i]}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color: isPos ? 'var(--up)' : 'var(--down)' }}>
              {isPos ? '+' : ''}{(r * 100).toFixed(1)}%
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Paper trade equity curve ──────────────────────────────────────────────────

function PaperEquitySVG({ data, initCapital }: { data: EquityPoint[]; initCapital: number }) {
  if (!data.length) return (
    <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)', fontSize: 12 }}>
      尚無交易記錄
    </div>
  );
  const W = 600, H = 180;
  const pad = { t: 12, r: 12, b: 24, l: 64 };
  const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;

  const allVals = [initCapital, ...data.map(d => d.capital)];
  const minV = Math.min(...allVals) * 0.995;
  const maxV = Math.max(...allVals) * 1.005;
  const range = maxV - minV || 1;

  // include start point
  const pts = [{ date: '', capital: initCapital }, ...data];
  const toX = (i: number) => pad.l + (i / (pts.length - 1)) * iw;
  const toY = (v: number) => pad.t + ih - ((v - minV) / range) * ih;

  const linePath = pts.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i).toFixed(1)},${toY(d.capital).toFixed(1)}`).join(' ');
  const fillPath = `${linePath} L${toX(pts.length - 1)},${H - pad.b} L${pad.l},${H - pad.b} Z`;
  const lastCapital = data[data.length - 1].capital;
  const isProfit = lastCapital >= initCapital;
  const lineColor = isProfit ? 'var(--up)' : 'var(--down)';

  const yTicks = [minV, (minV + maxV) / 2, maxV];
  const baseline = toY(initCapital);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H }}>
      <defs>
        <linearGradient id="paper-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={lineColor} stopOpacity="0.18" />
          <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* zero line */}
      <line x1={pad.l} y1={baseline} x2={W - pad.r} y2={baseline}
        stroke="var(--muted)" strokeWidth={0.8} strokeDasharray="3,3" />
      {yTicks.map(v => (
        <g key={v}>
          <line x1={pad.l} y1={toY(v)} x2={W - pad.r} y2={toY(v)} stroke="var(--border)" strokeWidth={0.5} />
          <text x={pad.l - 4} y={toY(v) + 4} textAnchor="end" fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">
            {v.toLocaleString('zh-TW', { maximumFractionDigits: 0 })}
          </text>
        </g>
      ))}
      <path d={fillPath} fill="url(#paper-grad)" />
      <path d={linePath} stroke={lineColor} strokeWidth={2} fill="none" />
      {/* x-axis labels */}
      {data.filter((_, i) => i % Math.max(1, Math.floor(data.length / 5)) === 0 || i === data.length - 1).map((d) => {
        const idx = pts.findIndex(p => p.date === d.date);
        return (
          <text key={d.date} x={toX(idx)} y={H - pad.b + 14} textAnchor="middle" fontSize={9} fill="var(--muted)" fontFamily="var(--font-mono)">
            {d.date.slice(5)}
          </text>
        );
      })}
    </svg>
  );
}

// ── Outcome helpers ───────────────────────────────────────────────────────────

const OUTCOME_ICON: Record<string, string> = {
  hit_target: '✅',
  hit_stop:   '❌',
  neutral:    '⬜',
  no_pick:    '—',
  no_capital: '🚫',
};
const OUTCOME_ZH: Record<string, string> = {
  hit_target: '達目標',
  hit_stop:   '觸停損',
  neutral:    '收盤出',
  no_pick:    '無推薦',
  no_capital: '資金不足',
};

// ── Paper trade tab ───────────────────────────────────────────────────────────

function PaperTradeTab() {
  const [summary, setSummary] = useState<PaperSummary | null>(null);
  const [history, setHistory] = useState<PaperRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const [s, h] = await Promise.all([
          fetch('/api/paper-trade/summary', { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } }).then(r => r.json()),
          fetch('/api/paper-trade/history',  { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } }).then(r => r.json()),
        ]);
        setSummary(s);
        setHistory(h);
      } catch (e) {
        setError('載入失敗，請確認後端已啟動');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)' }}>
      載入中...
    </div>
  );

  if (error) return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--down)' }}>
      {error}
    </div>
  );

  // Empty state
  if (!summary || !history.length) return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, color: 'var(--muted)' }}>
      <div style={{ fontSize: 40 }}>📊</div>
      <div style={{ fontSize: 14 }}>尚無模擬倉記錄</div>
      <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)', background: 'var(--surface)', padding: '8px 16px', borderRadius: 2, border: '1px solid var(--border)' }}>
        python3 dt_paper_trade.py --run
      </div>
      <div style={{ fontSize: 11, color: 'var(--muted)' }}>每日 13:35 由 post_market.sh 自動執行</div>
    </div>
  );

  const s = summary;
  const pnlColor = s.total_pnl >= 0 ? 'var(--up)' : 'var(--down)';
  const pnlSign = s.total_pnl >= 0 ? '+' : '';

  const kpis = [
    { label: '起始本金', value: `${s.initial_capital.toLocaleString()}`, unit: '元', color: 'var(--fg)' },
    { label: '目前本金', value: `${s.current_capital.toLocaleString()}`, unit: '元', color: pnlColor },
    { label: '累計損益', value: `${pnlSign}${s.total_pnl.toLocaleString()}`, unit: `(${pnlSign}${(s.total_pnl_pct * 100).toFixed(1)}%)`, color: pnlColor },
    { label: '勝率', value: `${(s.win_rate * 100).toFixed(1)}%`, unit: `${s.hits}達/${s.stops}停/${s.neutrals}收`, color: 'var(--fg)' },
    { label: '最大回撤', value: `${(s.max_drawdown * 100).toFixed(1)}%`, unit: '', color: 'var(--down)' },
    { label: '期望值', value: `${s.expectancy >= 0 ? '+' : ''}${s.expectancy.toLocaleString('zh-TW', { maximumFractionDigits: 0 })}`, unit: '元/筆', color: s.expectancy >= 0 ? 'var(--up)' : 'var(--down)' },
    { label: '交易天數', value: `${s.total_days}`, unit: `有效 ${s.real_trades} 筆`, color: 'var(--fg)' },
  ];

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* KPI strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 1, background: 'var(--border)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        {kpis.map(k => (
          <div key={k.label} style={{ background: 'var(--bg)', padding: '10px 10px', textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 4, fontFamily: 'var(--font-mono)', letterSpacing: 0.4 }}>{k.label}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 700, color: k.color }}>{k.value}</div>
            {k.unit && <div style={{ fontSize: 9, color: 'var(--muted)', marginTop: 2, fontFamily: 'var(--font-mono)' }}>{k.unit}</div>}
          </div>
        ))}
      </div>

      {/* Body */}
      <div style={{ flex: 1, display: 'flex', gap: 1, background: 'var(--border)', overflow: 'hidden' }}>
        {/* Left: equity curve + stats */}
        <div style={{ flex: '1.4', background: 'var(--bg)', padding: 16, display: 'flex', flexDirection: 'column', gap: 16, overflowY: 'auto' }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 8, letterSpacing: 1 }}>
              EQUITY CURVE · 本金走勢
            </div>
            <PaperEquitySVG data={s.equity_curve} initCapital={s.initial_capital} />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 2, padding: '10px 14px' }}>
              <div style={{ fontSize: 9, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 6 }}>平均損益</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--up)', marginBottom: 4 }}>
                獲利 +{s.avg_win.toLocaleString('zh-TW', { maximumFractionDigits: 0 })} 元
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--down)' }}>
                停損 {s.avg_loss.toLocaleString('zh-TW', { maximumFractionDigits: 0 })} 元
              </div>
            </div>
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 2, padding: '10px 14px' }}>
              <div style={{ fontSize: 9, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 6 }}>連勝 / 連敗</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--up)', marginBottom: 4 }}>
                最長連勝 {s.streak_win} 天
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--down)' }}>
                最長連敗 {s.streak_loss} 天
              </div>
            </div>
          </div>

          <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
            起始日期 {s.start_date} · 無推薦日 {s.no_trade_days} 天
          </div>
        </div>

        {/* Right: trade log */}
        <div style={{ flex: 1, background: 'var(--bg)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '12px 16px 8px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1 }}>TRADE LOG · 交易明細</div>
          </div>

          {/* header row */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '80px 58px 1fr 60px 62px 62px 72px',
            gap: '4px 6px',
            padding: '6px 16px',
            fontSize: 9,
            color: 'var(--muted)',
            fontFamily: 'var(--font-mono)',
            borderBottom: '1px solid var(--border)',
            flexShrink: 0,
          }}>
            <span>日期</span>
            <span>代碼</span>
            <span>名稱</span>
            <span>結果</span>
            <span style={{ textAlign: 'right' }}>進場</span>
            <span style={{ textAlign: 'right' }}>出場</span>
            <span style={{ textAlign: 'right' }}>損益(元)</span>
          </div>

          {/* rows */}
          <div style={{ flex: 1, overflowY: 'auto' }}>
            {[...history].reverse().map(t => {
              const icon = OUTCOME_ICON[t.outcome ?? ''] ?? '?';
              const zh   = OUTCOME_ZH[t.outcome ?? ''] ?? '';
              const pnlC = t.pnl >= 0 ? 'var(--up)' : 'var(--down)';
              const isNoTrade = t.outcome === 'no_pick' || t.outcome === 'no_capital';

              return (
                <div key={t.id} style={{
                  display: 'grid',
                  gridTemplateColumns: '80px 58px 1fr 60px 62px 62px 72px',
                  gap: '4px 6px',
                  padding: '5px 16px',
                  fontSize: 11,
                  fontFamily: 'var(--font-mono)',
                  borderBottom: '1px solid var(--border)',
                  opacity: isNoTrade ? 0.5 : 1,
                }}>
                  <span style={{ color: 'var(--muted)' }}>{t.date}</span>
                  <span style={{ color: 'var(--fg)' }}>{t.code ?? '—'}</span>
                  <span style={{ color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.name ?? zh}</span>
                  <span style={{ color: t.outcome === 'hit_target' ? 'var(--up)' : t.outcome === 'hit_stop' ? 'var(--down)' : 'var(--muted)' }}>
                    {icon} {zh}
                  </span>
                  <span style={{ textAlign: 'right', color: 'var(--muted)' }}>
                    {t.entry_price != null ? t.entry_price.toFixed(1) : '—'}
                  </span>
                  <span style={{ textAlign: 'right', color: 'var(--muted)' }}>
                    {t.exit_price != null ? t.exit_price.toFixed(1) : '—'}
                  </span>
                  <span style={{ textAlign: 'right', color: isNoTrade ? 'var(--muted)' : pnlC, fontWeight: 600 }}>
                    {isNoTrade ? '—' : `${t.pnl >= 0 ? '+' : ''}${t.pnl.toLocaleString()}`}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Backtest tab ──────────────────────────────────────────────────────────────

function BacktestTab() {
  const [params, setParams] = useState<BacktestParams>({
    start: '2026-01-01',
    end: '2026-05-29',
    strategy: 'AI TopN v2.1',
    initial_capital: 1000000,
    slippage: 0.1,
  });
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult>(MOCK_RESULT);
  const [editing, setEditing] = useState(false);

  const kpis = [
    { label: '累計報酬', value: `+${(result.total_pnl_pct * 100).toFixed(2)}%`, color: 'var(--up)' },
    { label: 'vs 大盤', value: `+${(result.beat_index_pct * 100).toFixed(2)}%`, color: 'var(--up)' },
    { label: '勝率', value: `${(result.winrate * 100).toFixed(1)}%`, color: 'var(--fg)' },
    { label: 'Sharpe', value: result.sharpe.toFixed(2), color: 'var(--fg)' },
    { label: '最大回撤', value: `${(result.max_dd * 100).toFixed(1)}%`, color: 'var(--down)' },
    { label: '平均盈利', value: `+${result.avg_win.toLocaleString()}`, color: 'var(--up)' },
    { label: '平均虧損', value: result.avg_loss.toLocaleString(), color: 'var(--down)' },
  ];

  async function runBacktest() {
    setRunning(true);
    try {
      const res = await api.runBacktest(params);
      setResult(res);
    } catch {
      // keep mock data
    } finally {
      setRunning(false);
      setEditing(false);
    }
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Params bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '10px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface)', flexWrap: 'wrap', flexShrink: 0 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--muted)' }}>{result.id}</span>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{params.start} ~ {params.end}</span>
        {STRATEGIES.map(s => (
          <button key={s} onClick={() => setParams(p => ({ ...p, strategy: s }))}
            style={{ padding: '3px 10px', borderRadius: 2, fontSize: 11, border: `1px solid ${params.strategy === s ? 'var(--accent)' : 'var(--border)'}`, background: params.strategy === s ? 'var(--accent)' : 'var(--surface)', color: params.strategy === s ? '#fff' : 'var(--fg)', cursor: 'pointer' }}>
            {s}
          </button>
        ))}
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>初始 {(params.initial_capital / 10000).toFixed(0)}萬 · 滑價 {params.slippage}%</span>
        <button onClick={() => setEditing(!editing)} style={{ padding: '4px 12px', borderRadius: 2, fontSize: 11, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--fg)', cursor: 'pointer' }}>修改參數</button>
        <button onClick={runBacktest} disabled={running} style={{ padding: '4px 16px', borderRadius: 2, fontSize: 11, border: 'none', background: running ? 'var(--muted)' : 'var(--accent)', color: '#fff', cursor: running ? 'default' : 'pointer' }}>
          {running ? '執行中...' : '重新執行'}
        </button>
      </div>

      {editing && (
        <div style={{ padding: 16, background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', flexShrink: 0 }}>
          <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', flexDirection: 'column', gap: 4 }}>
            開始日期
            <input type="date" value={params.start} onChange={e => setParams(p => ({ ...p, start: e.target.value }))}
              style={{ padding: '4px 8px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--fg)', borderRadius: 2, fontSize: 12 }} />
          </label>
          <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', flexDirection: 'column', gap: 4 }}>
            結束日期
            <input type="date" value={params.end} onChange={e => setParams(p => ({ ...p, end: e.target.value }))}
              style={{ padding: '4px 8px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--fg)', borderRadius: 2, fontSize: 12 }} />
          </label>
          <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', flexDirection: 'column', gap: 4 }}>
            初始資金 (萬)
            <input type="number" value={params.initial_capital / 10000} onChange={e => setParams(p => ({ ...p, initial_capital: Number(e.target.value) * 10000 }))}
              style={{ padding: '4px 8px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--fg)', borderRadius: 2, fontSize: 12, width: 80 }} />
          </label>
          <label style={{ fontSize: 11, color: 'var(--muted)', display: 'flex', flexDirection: 'column', gap: 4 }}>
            滑價 (%)
            <input type="number" step="0.05" value={params.slippage} onChange={e => setParams(p => ({ ...p, slippage: Number(e.target.value) }))}
              style={{ padding: '4px 8px', background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--fg)', borderRadius: 2, fontSize: 12, width: 70 }} />
          </label>
        </div>
      )}

      {/* KPI strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 1, background: 'var(--border)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        {kpis.map(kpi => (
          <div key={kpi.label} style={{ background: 'var(--bg)', padding: '10px 14px', textAlign: 'center' }}>
            <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 4, fontFamily: 'var(--font-mono)', letterSpacing: 0.5 }}>{kpi.label}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700, color: kpi.color }}>{kpi.value}</div>
          </div>
        ))}
      </div>

      {/* Main body */}
      <div style={{ display: 'flex', gap: 1, background: 'var(--border)', flex: 1, minHeight: 0, overflowY: 'auto' }}>
        <div style={{ flex: '1.6', background: 'var(--bg)', padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 8 }}>EQUITY CURVE · 策略 vs 大盤</div>
            <BacktestEquitySVG data={result.equity_curve} />
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 8 }}>MONTHLY RETURNS · 月度報酬</div>
            <MonthlyHeatmap returns={result.monthly_returns} />
          </div>
          <div style={{ display: 'flex', gap: 24, paddingTop: 8 }}>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>總交易 <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{result.trades}</span></div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>獲利 <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--up)' }}>{result.wins}</span></div>
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>虧損 <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--down)' }}>{result.losses}</span></div>
          </div>
        </div>
        <div style={{ flex: 1, background: 'var(--bg)', display: 'flex', flexDirection: 'column', gap: 1 }}>
          <div style={{ padding: 16 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 8 }}>SAMPLE TRADES · 交易明細</div>
            <div style={{ display: 'grid', gridTemplateColumns: '50px 60px 1fr 70px 70px', gap: '4px 8px', fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', borderBottom: '1px solid var(--border)', paddingBottom: 4, marginBottom: 4 }}>
              <span>日期</span><span>代碼</span><span>名稱</span><span>方向</span><span style={{ textAlign: 'right' }}>損益</span>
            </div>
            {[
              { date: '05/28', code: '2330', name: '台積電', side: '買', pnl: 8400 },
              { date: '05/28', code: '2454', name: '聯發科', side: '買', pnl: -3200 },
              { date: '05/27', code: '2382', name: '廣達',   side: '買', pnl: 5600 },
              { date: '05/27', code: '2317', name: '鴻海',   side: '買', pnl: -1800 },
              { date: '05/26', code: '2308', name: '台達電', side: '買', pnl: 12300 },
              { date: '05/26', code: '2303', name: '聯電',   side: '買', pnl: -900 },
              { date: '05/23', code: '2345', name: '智邦',   side: '買', pnl: 3400 },
              { date: '05/23', code: '2412', name: '中華電', side: '買', pnl: -1200 },
              { date: '05/22', code: '2881', name: '富邦金', side: '買', pnl: 6700 },
              { date: '05/22', code: '2886', name: '兆豐金', side: '買', pnl: 2100 },
            ].map((t, i) => (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: '50px 60px 1fr 70px 70px', gap: '4px 8px', fontSize: 11, fontFamily: 'var(--font-mono)', padding: '3px 0', borderBottom: '1px solid var(--border)' }}>
                <span style={{ color: 'var(--muted)' }}>{t.date}</span>
                <span>{t.code}</span>
                <span style={{ color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.name}</span>
                <span style={{ color: 'var(--muted)' }}>{t.side}</span>
                <span style={{ textAlign: 'right', color: t.pnl >= 0 ? 'var(--up)' : 'var(--down)' }}>
                  {t.pnl >= 0 ? '+' : ''}{t.pnl.toLocaleString()}
                </span>
              </div>
            ))}
          </div>
          <div style={{ background: '#0d1117', padding: 16, margin: '0 1px 1px', borderRadius: 2 }}>
            <div style={{ fontSize: 9, color: '#666', fontFamily: 'var(--font-mono)', letterSpacing: 1, marginBottom: 8 }}>AI CONCLUSION</div>
            <p style={{ fontSize: 12, color: '#ccc', lineHeight: 1.7, margin: 0, whiteSpace: 'pre-line' }}>{result.ai_conclusion}</p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

type TabId = 'paper' | 'backtest';

export default function Simulate() {
  const [tab, setTab] = useState<TabId>('paper');

  const tabs: { id: TabId; label: string; badge?: string }[] = [
    { id: 'paper',   label: '模擬倉', badge: 'LIVE' },
    { id: 'backtest', label: '回測' },
  ];

  return (
    <AppChrome title="回測 / 模擬倉" eyebrow="05.3">
      {/* Tab bar */}
      <div style={{
        display: 'flex',
        gap: 0,
        borderBottom: '1px solid var(--border)',
        background: 'var(--surface)',
        flexShrink: 0,
      }}>
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              padding: '10px 20px',
              fontSize: 12,
              fontWeight: 600,
              border: 'none',
              borderBottom: tab === t.id ? '2px solid var(--accent)' : '2px solid transparent',
              background: 'transparent',
              color: tab === t.id ? 'var(--fg)' : 'var(--muted)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            {t.label}
            {t.badge && (
              <span style={{
                fontSize: 8,
                fontFamily: 'var(--font-mono)',
                background: 'var(--accent)',
                color: '#fff',
                padding: '1px 5px',
                borderRadius: 2,
                letterSpacing: 0.5,
              }}>
                {t.badge}
              </span>
            )}
          </button>
        ))}
      </div>

      {tab === 'paper'    && <PaperTradeTab />}
      {tab === 'backtest' && <BacktestTab />}
    </AppChrome>
  );
}
