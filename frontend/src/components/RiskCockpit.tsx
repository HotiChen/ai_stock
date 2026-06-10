// Shared component — Risk Cockpit panel
// Extracted from pages/daytrade/Cockpit.tsx (RiskPanel)
import Pill from './Pill';
import type { RiskCockpit as RiskCockpitData } from '../types';

// ── Number formatters (local, only used here) ───────────────────
function fmtPrice(n: number) {
  return n.toLocaleString('zh-TW');
}
function fmtPnl(n: number) {
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toLocaleString('zh-TW')}`;
}

// ── Props ────────────────────────────────────────────────────────
export interface RiskCockpitProps {
  risk: RiskCockpitData;
}

// ── Component ────────────────────────────────────────────────────
export default function RiskCockpit({ risk }: RiskCockpitProps) {
  const ddPct = Math.abs(risk.intraday_pnl / risk.budget);
  const ddLimit = Math.abs(risk.daily_max_dd_limit / risk.budget);
  const ddBarPct = Math.min(1, ddPct / ddLimit);

  return (
    <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* 資金使用率 */}
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', marginBottom: 4 }}>
          <span>資金使用率</span>
          <span style={{ color: risk.utilization > 0.8 ? 'var(--up)' : 'var(--ink)' }}>
            {(risk.utilization * 100).toFixed(1)}% · {fmtPrice(risk.used)} / {fmtPrice(risk.budget)}
          </span>
        </div>
        <div style={{ height: 6, background: 'var(--hair)', borderRadius: 1 }}>
          <div style={{
            height: '100%',
            width: `${risk.utilization * 100}%`,
            background: risk.utilization > 0.8 ? 'var(--up)' : 'var(--gold)',
            borderRadius: 1,
            transition: 'width 0.5s',
          }} />
        </div>
      </div>

      {/* 日內 PnL */}
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', marginBottom: 4 }}>
          <span>日內最大虧損</span>
          <span style={{ color: risk.intraday_pnl >= 0 ? 'var(--down)' : 'var(--up)' }}>
            {fmtPnl(risk.intraday_pnl)} / 上限 {fmtPrice(risk.daily_max_dd_limit)}
          </span>
        </div>
        <div style={{ height: 6, background: 'var(--hair)', borderRadius: 1, overflow: 'hidden' }}>
          <div style={{
            height: '100%',
            width: `${ddBarPct * 100}%`,
            background: ddBarPct > 0.8 ? 'var(--up)' : 'var(--down)',
            marginLeft: 'auto',
            borderRadius: 1,
          }} />
        </div>
      </div>

      {/* 板塊集中度 */}
      <div>
        <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', marginBottom: 6, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
          板塊集中度
        </div>
        {risk.sector_allocation.map((s) => {
          const overLimit = s.ratio > s.limit;
          return (
            <div key={s.name} style={{ marginBottom: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', marginBottom: 2 }}>
                <span>{s.name}</span>
                <span style={{ color: overLimit ? 'var(--up)' : 'var(--ink-2)' }}>
                  {(s.ratio * 100).toFixed(0)}% {overLimit ? '▲超限' : `/ ${(s.limit * 100).toFixed(0)}%`}
                </span>
              </div>
              <div style={{ height: 4, background: 'var(--hair)', borderRadius: 1 }}>
                <div style={{
                  height: '100%',
                  width: `${Math.min(100, s.ratio * 100)}%`,
                  background: overLimit ? 'var(--up)' : 'var(--gold)',
                  borderRadius: 1,
                }} />
              </div>
            </div>
          );
        })}
      </div>

      {/* 黑名單 */}
      {risk.blacklist.length > 0 && (
        <div>
          <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', marginBottom: 6, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            黑名單
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {risk.blacklist.map((code) => (
              <Pill key={code} color="var(--up)" bg="var(--up-soft)" border="var(--up)" size={10}>
                {code}
              </Pill>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
