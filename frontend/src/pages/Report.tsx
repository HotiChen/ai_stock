// 05.4 週報
import { useQuery } from '@tanstack/react-query';
import AppChrome from '../components/AppChrome';
import { api } from '../api';
import type { WeeklyReport } from '../types';

const MOCK_REPORT: WeeklyReport = {
  week_label: 'Week 21 · 2026',
  narrative: '本週市場在半導體板塊強勢帶動下，AI 選股策略表現優異。五個交易日中有四日獲利，整體勝率達 68.4%，超越近四週平均水準（61.2%）。台積電突破前高帶動整個供應鏈，廣達、台達電等 AI 伺服器相關股也同步走強。\n\n風險面：聯電在大盤疲弱時進場虧損，後續應加強大盤環境過濾；鴻海追高失敗提醒我們等確認信號的重要性。整體而言本週淨損益 +18,420，達成月度目標 72.4%。',
  net_pnl: 18420,
  net_pnl_pct: 0.0154,
  winrate: 0.684,
  trades: 19,
  best_trade_pnl: 12300,
  avg_daily_pnl: 3684,
  daily_details: [
    { date: '2026-05-26', weekday: '一', pnl: 11400, trades: 4, winrate: 0.75 },
    { date: '2026-05-27', weekday: '二', pnl: 3800, trades: 4, winrate: 0.75 },
    { date: '2026-05-28', weekday: '三', pnl: 5200, trades: 4, winrate: 0.5 },
    { date: '2026-05-29', weekday: '四', pnl: -980, trades: 4, winrate: 0.5 },
    { date: '2026-05-30', weekday: '五', pnl: 0, trades: 3, winrate: 0.667 },
  ],
  confidence_winrate: [
    { tier: '高信心 ≥0.80', winrate: 0.857, trades: 7 },
    { tier: '中高 0.75–0.79', winrate: 0.714, trades: 7 },
    { tier: '中 0.65–0.74', winrate: 0.6, trades: 5 },
    { tier: '低 <0.65', winrate: 0.0, trades: 0 },
  ],
  sector_winrate: [
    { sector: '半導體', winrate: 0.833, trades: 6 },
    { sector: 'AI伺服器', winrate: 0.75, trades: 4 },
    { sector: '金融', winrate: 0.667, trades: 3 },
    { sector: '電信', winrate: 0.5, trades: 2 },
    { sector: '電子零件', winrate: 0.5, trades: 4 },
  ],
  next_week_suggestions: [
    '提高最小信心門檻至 0.78，過濾低勝率交易',
    '加入大盤過濾：TAIEX 日內跌幅 > 0.5% 時暫停進場',
    '半導體板塊保持高配比，AI 伺服器次之',
    '追高停損改為 2%，避免重演鴻海案例',
  ],
};

function DailyBar({ pnl, maxAbs }: { pnl: number; maxAbs: number }) {
  const pct = maxAbs > 0 ? Math.abs(pnl) / maxAbs : 0;
  const isPos = pnl >= 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 80, height: 6, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct * 100}%`, background: isPos ? 'var(--up)' : 'var(--down)', borderRadius: 2 }} />
      </div>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: isPos ? 'var(--up)' : 'var(--down)', minWidth: 60 }}>
        {isPos ? '+' : ''}{pnl.toLocaleString()}
      </span>
    </div>
  );
}

function WinrateBar({ rate }: { rate: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 80, height: 6, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${rate * 100}%`, background: 'var(--accent)', borderRadius: 2 }} />
      </div>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--muted)', minWidth: 32 }}>{(rate * 100).toFixed(0)}%</span>
    </div>
  );
}

export default function Report() {
  const { data: report = MOCK_REPORT } = useQuery({
    queryKey: ['report'],
    queryFn: () => api.getWeeklyReport(),
    retry: false,
  });

  const maxAbsPnl = Math.max(...report.daily_details.map(d => Math.abs(d.pnl)));

  return (
    <AppChrome title="週報" eyebrow="05.4">
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 1, background: 'var(--border)' }}>

        {/* 1. Hero */}
        <div style={{ display: 'flex', gap: 1 }}>
          {/* Left white panel */}
          <div style={{ flex: 2, background: 'var(--bg)', padding: 24 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1, marginBottom: 8 }}>WEEKLY REPORT</div>
            <div style={{ fontSize: 28, fontWeight: 700, marginBottom: 16 }}>{report.week_label}</div>
            <p style={{ fontSize: 13, lineHeight: 1.8, color: 'var(--fg)', margin: 0, whiteSpace: 'pre-line' }}>
              {report.narrative.replace(/\+1\.54%/g, '').replace(/68\.4%/g, '').split('\n').map((line, i) => {
                const parts = line.split(/(\+[\d.]+%|[\d.]+%|\+[\d,]+)/g);
                return (
                  <span key={i}>
                    {parts.map((p, j) => {
                      if (p.startsWith('+') || (p.match(/^[\d.]+%$/) && parseFloat(p) > 5)) {
                        const isPos = p.startsWith('+');
                        return <span key={j} style={{ color: isPos ? 'var(--up)' : 'var(--fg)', fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{p}</span>;
                      }
                      return p;
                    })}
                    {i < report.narrative.split('\n').length - 1 && <br />}
                  </span>
                );
              })}
            </p>
          </div>

          {/* Right dark panel */}
          <div style={{ flex: 1, background: '#0d1117', padding: 24 }}>
            <div style={{ fontSize: 10, color: '#666', fontFamily: 'var(--font-mono)', letterSpacing: 1, marginBottom: 12 }}>本週淨損益</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 36, fontWeight: 800, color: 'var(--up)', marginBottom: 4 }}>
              +{report.net_pnl.toLocaleString()}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--up)', marginBottom: 24 }}>
              +{(report.net_pnl_pct * 100).toFixed(2)}%
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {[
                { label: '勝率', value: `${(report.winrate * 100).toFixed(1)}%` },
                { label: '交易筆數', value: `${report.trades}` },
                { label: '最佳單筆', value: `+${report.best_trade_pnl.toLocaleString()}` },
                { label: '日均損益', value: `+${report.avg_daily_pnl.toLocaleString()}` },
              ].map(kpi => (
                <div key={kpi.label}>
                  <div style={{ fontSize: 9, color: '#666', fontFamily: 'var(--font-mono)', marginBottom: 3 }}>{kpi.label}</div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 600, color: '#ccc' }}>{kpi.value}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* 2. Daily details */}
        <div style={{ background: 'var(--bg)', padding: 20 }}>
          <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1, marginBottom: 12 }}>DAILY BREAKDOWN · 每日明細</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
            {report.daily_details.map(day => {
              const isPos = day.pnl >= 0;
              return (
                <div key={day.date} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <div style={{ width: 3, background: isPos ? 'var(--up)' : 'var(--down)', borderRadius: 2, alignSelf: 'stretch', flexShrink: 0 }} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 6 }}>
                      {day.date.slice(5)} ({day.weekday})
                    </div>
                    <DailyBar pnl={day.pnl} maxAbs={maxAbsPnl} />
                    <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6, display: 'flex', gap: 10 }}>
                      <span>{day.trades} 筆</span>
                      <span>勝率 {(day.winrate * 100).toFixed(0)}%</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* 3. Three-column analysis */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 1 }}>
          {/* Confidence winrate */}
          <div style={{ background: 'var(--bg)', padding: 20 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1, marginBottom: 12 }}>信心分層勝率</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {report.confidence_winrate.map(row => (
                <div key={row.tier}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 11, color: 'var(--fg)' }}>{row.tier}</span>
                    <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>{row.trades} 筆</span>
                  </div>
                  <WinrateBar rate={row.winrate} />
                </div>
              ))}
            </div>
          </div>

          {/* Sector winrate */}
          <div style={{ background: 'var(--bg)', padding: 20 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1, marginBottom: 12 }}>板塊勝率</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {report.sector_winrate.map(row => (
                <div key={row.sector}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 11, color: 'var(--fg)' }}>{row.sector}</span>
                    <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>{row.trades} 筆</span>
                  </div>
                  <WinrateBar rate={row.winrate} />
                </div>
              ))}
            </div>
          </div>

          {/* Next week suggestions */}
          <div style={{ background: 'var(--bg)', padding: 20 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1, marginBottom: 12 }}>下週調整建議</div>
            <ol style={{ padding: 0, margin: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {report.next_week_suggestions.map((s, i) => (
                <li key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--accent)', fontWeight: 700, flexShrink: 0, minWidth: 16 }}>{i + 1}.</span>
                  <span style={{ fontSize: 12, color: 'var(--fg)', lineHeight: 1.5 }}>{s}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>
    </AppChrome>
  );
}
