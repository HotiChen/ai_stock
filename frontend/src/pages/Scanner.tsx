// 05.5 大盤掃描
import { useQuery } from '@tanstack/react-query';
import AppChrome from '../components/AppChrome';
import { api } from '../api';
import type { MarketScan } from '../types';

const MOCK_SCAN: MarketScan = {
  indices: {
    taiex: { name: '加權指數', value: 22847.3, change: 124.5, change_pct: 0.0054 },
    otc: { name: 'OTC 指數', value: 312.4, change: 2.1, change_pct: 0.0067 },
    tsmc_adr: { name: 'TSMC ADR', value: 187.2, change: 3.4, change_pct: 0.0185 },
    usd_twd: { name: 'USD/TWD', value: 30.84, change: -0.12, change_pct: -0.0039 },
    vix: { name: 'VIX', value: 16.8, change: -0.9, change_pct: -0.0509 },
    futures: { name: '台指期', value: 22910, change: 62.7, change_pct: 0.00274 },
  },
  sectors: [
    { name: '半導體', count: 48, change_pct: 0.0218, up_count: 38, down_count: 10, leaders: ['2330', '2454', '2379'] },
    { name: 'AI伺服器', count: 22, change_pct: 0.0312, up_count: 19, down_count: 3, leaders: ['2382', '2308', '3008'] },
    { name: '金融保險', count: 42, change_pct: 0.0087, up_count: 28, down_count: 14, leaders: ['2881', '2882', '2886'] },
    { name: '電信', count: 12, change_pct: -0.0023, up_count: 5, down_count: 7, leaders: ['2412', '4904'] },
    { name: '電子零件', count: 35, change_pct: 0.0145, up_count: 26, down_count: 9, leaders: ['2317', '2354', '2353'] },
    { name: '生技醫療', count: 28, change_pct: -0.0067, up_count: 10, down_count: 18, leaders: ['4711', '6547'] },
    { name: '航運', count: 15, change_pct: 0.0034, up_count: 9, down_count: 6, leaders: ['2603', '2609'] },
    { name: '鋼鐵', count: 18, change_pct: -0.0112, up_count: 6, down_count: 12, leaders: ['2002', '2006'] },
  ],
  ai_signals: [
    { code: '2330', name: '台積電', sector: '半導體', signal: 'buy', confidence: 0.89, target_price: 1120, stop_loss_price: 1030, last_price: 1068, change_pct: 0.018, spark: [1020, 1035, 1048, 1062, 1068], reason: '突破前高，AI 看多', tags: ['突破', '量能'], action: 'pending', budget: 200000, budget_ratio: 0.2, run_id: 'r1', created_at: '2026-05-29T08:30:00' },
    { code: '2382', name: '廣達', sector: 'AI伺服器', signal: 'buy', confidence: 0.81, target_price: 320, stop_loss_price: 290, last_price: 305, change_pct: 0.031, spark: [278, 285, 292, 298, 305], reason: 'AI 伺服器訂單持續增長', tags: ['成長', '法人'], action: 'pending', budget: 150000, budget_ratio: 0.15, run_id: 'r1', created_at: '2026-05-29T08:30:00' },
    { code: '2308', name: '台達電', sector: 'AI伺服器', signal: 'buy', confidence: 0.77, target_price: 405, stop_loss_price: 375, last_price: 391, change_pct: 0.022, spark: [368, 374, 381, 387, 391], reason: '電源供應模組出貨創高', tags: ['法人', '量能'], action: 'pending', budget: 130000, budget_ratio: 0.13, run_id: 'r1', created_at: '2026-05-29T08:30:00' },
    { code: '2881', name: '富邦金', sector: '金融保險', signal: 'buy', confidence: 0.72, target_price: 92, stop_loss_price: 83, last_price: 87.3, change_pct: 0.009, spark: [82, 83.5, 85, 86.2, 87.3], reason: '升息循環末段，壽險受益', tags: ['殖利率', '防禦'], action: 'pending', budget: 100000, budget_ratio: 0.1, run_id: 'r1', created_at: '2026-05-29T08:30:00' },
  ],
  news: [
    { id: 'n1', source: '財訊', time: '09:15', headline: '台積電 CoWoS 產能明年擴充 50%，AI 晶片需求旺盛拉動供應鏈', url: '' },
    { id: 'n2', source: 'CNBC', time: '08:42', headline: 'NVIDIA 財報超預期，台系 AI 伺服器供應鏈廣達、緯創同步受益', url: '' },
    { id: 'n3', source: '工商時報', time: '08:15', headline: '央行維持利率不變，新台幣升值壓力稍緩，外資今日回流 8.2 億', url: '' },
  ],
};

function IndexCard({ index }: { index: { name: string; value: number; change: number; change_pct: number } }) {
  const isPos = index.change >= 0;
  return (
    <div style={{ background: 'var(--bg)', padding: '12px 16px' }}>
      <div style={{ fontSize: 9, color: 'var(--muted)', fontFamily: 'var(--font-mono)', marginBottom: 6, letterSpacing: 0.5 }}>{index.name}</div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 700 }}>{index.value.toLocaleString('zh-TW', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: isPos ? 'var(--up)' : 'var(--down)', marginTop: 4 }}>
        {isPos ? '+' : ''}{index.change.toFixed(2)} ({isPos ? '+' : ''}{(index.change_pct * 100).toFixed(2)}%)
      </div>
    </div>
  );
}

function SectorRow({ sector }: { sector: MarketScan['sectors'][0] }) {
  const isPos = sector.change_pct >= 0;
  const total = sector.up_count + sector.down_count;
  const upRatio = total > 0 ? sector.up_count / total : 0;
  const intensity = Math.min(Math.abs(sector.change_pct) / 0.03, 1);
  const bg = isPos
    ? `rgba(200, 51, 43, ${0.05 + intensity * 0.15})`
    : `rgba(46, 125, 79, ${0.05 + intensity * 0.15})`;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '90px 50px 60px 1fr 120px', gap: 8, alignItems: 'center', padding: '8px 12px', background: bg, borderBottom: '1px solid var(--border)' }}>
      <span style={{ fontSize: 12, fontWeight: 600 }}>{sector.name}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--muted)', textAlign: 'center' }}>{sector.count}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 700, color: isPos ? 'var(--up)' : 'var(--down)', textAlign: 'right' }}>
        {isPos ? '+' : ''}{(sector.change_pct * 100).toFixed(2)}%
      </span>
      <div style={{ display: 'flex', height: 8, borderRadius: 2, overflow: 'hidden', gap: 1 }}>
        <div style={{ flex: upRatio, background: 'var(--up)', borderRadius: '2px 0 0 2px' }} />
        <div style={{ flex: 1 - upRatio, background: 'var(--down)', borderRadius: '0 2px 2px 0' }} />
      </div>
      <div style={{ display: 'flex', gap: 4 }}>
        {sector.leaders.slice(0, 3).map(code => (
          <span key={code} style={{ fontFamily: 'var(--font-mono)', fontSize: 10, padding: '2px 5px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 2 }}>{code}</span>
        ))}
      </div>
    </div>
  );
}

function AISignalCard({ pick }: { pick: MarketScan['ai_signals'][0] }) {
  const isPos = pick.change_pct >= 0;
  const confidencePct = Math.round(pick.confidence * 100);
  const tier = pick.confidence >= 0.75 ? '高' : pick.confidence >= 0.60 ? '中' : '低';
  const tierColor = pick.confidence >= 0.75 ? 'var(--up)' : pick.confidence >= 0.60 ? '#d4a017' : 'var(--down)';

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 2, padding: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <div>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700 }}>{pick.code}</span>
          <span style={{ fontSize: 13, color: 'var(--fg)', marginLeft: 6 }}>{pick.name}</span>
          <span style={{ fontSize: 10, padding: '1px 6px', background: 'var(--border)', borderRadius: 2, marginLeft: 6, color: 'var(--muted)' }}>{pick.sector}</span>
        </div>
        <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 2, background: pick.signal === 'buy' ? 'var(--up)' : 'var(--down)', color: '#fff', fontFamily: 'var(--font-mono)', textTransform: 'uppercase' }}>
          {pick.signal}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700 }}>{pick.last_price.toLocaleString()}</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: isPos ? 'var(--up)' : 'var(--down)' }}>
          {isPos ? '+' : ''}{(pick.change_pct * 100).toFixed(2)}%
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto' }}>
          <span style={{ fontSize: 10, color: 'var(--muted)' }}>信心</span>
          <div style={{ width: 50, height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${confidencePct}%`, background: tierColor }} />
          </div>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: tierColor }}>{tier} {confidencePct}%</span>
        </div>
      </div>
      <p style={{ fontSize: 11, color: 'var(--muted)', margin: '8px 0 0', lineHeight: 1.4 }}>{pick.reason}</p>
    </div>
  );
}

export default function Scanner() {
  const { data: scan = MOCK_SCAN } = useQuery({
    queryKey: ['scanner'],
    queryFn: () => api.getScanner(),
    refetchInterval: 30000,
    retry: false,
  });

  const indices = [scan.indices.taiex, scan.indices.otc, scan.indices.tsmc_adr, scan.indices.usd_twd, scan.indices.vix, scan.indices.futures];

  return (
    <AppChrome title="大盤掃描" eyebrow="05.5">
      {/* Index strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 1, background: 'var(--border)', borderBottom: '1px solid var(--border)' }}>
        {indices.map(idx => <IndexCard key={idx.name} index={idx} />)}
      </div>

      {/* Main body */}
      <div style={{ display: 'flex', gap: 1, background: 'var(--border)', flex: 1, minHeight: 0, overflowY: 'auto' }}>
        {/* Left: Sector heat table */}
        <div style={{ flex: '1.4', background: 'var(--bg)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '90px 50px 60px 1fr 120px', gap: 8, padding: '6px 12px', fontSize: 9, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 0.5, borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
            <span>板塊</span>
            <span style={{ textAlign: 'center' }}>檔數</span>
            <span style={{ textAlign: 'right' }}>漲跌%</span>
            <span>廣度</span>
            <span>領漲股</span>
          </div>
          {scan.sectors.map(sector => <SectorRow key={sector.name} sector={sector} />)}
          <div style={{ padding: '6px 12px', fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
            廣度 bar：左紅(漲) / 右綠(跌) · 每 30 秒更新
          </div>
        </div>

        {/* Right: AI signals + news */}
        <div style={{ flex: 1, background: 'var(--bg)', display: 'flex', flexDirection: 'column', gap: 1 }}>
          {/* AI signals */}
          <div style={{ padding: 16 }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1, marginBottom: 10 }}>AI 訊號掃描 · {scan.ai_signals.length} 檔</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {scan.ai_signals.map(pick => <AISignalCard key={pick.code} pick={pick} />)}
            </div>
          </div>

          {/* News */}
          <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border)' }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1, marginBottom: 10 }}>今日重要新聞</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              {scan.news.map(item => (
                <div key={item.id} style={{ display: 'flex', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--border)', alignItems: 'flex-start' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2, flexShrink: 0, minWidth: 60 }}>
                    <span style={{ fontSize: 9, color: 'var(--accent)', fontFamily: 'var(--font-mono)', letterSpacing: 0.5 }}>{item.source}</span>
                    <span style={{ fontSize: 9, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>{item.time}</span>
                  </div>
                  <p style={{ fontSize: 12, color: 'var(--fg)', margin: 0, lineHeight: 1.5, flex: 1 }}>{item.headline}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </AppChrome>
  );
}
