// 04.1 AI 當沖 · 駕駛艙 — Wave 3-H
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import AppChrome from '../../components/AppChrome';
import CountdownBar from '../../components/CountdownBar';
import AlertRow from '../../components/AlertRow';
import ThreadRow from '../../components/ThreadRow';
import DistRangeBar from '../../components/DistRangeBar';
import Pill from '../../components/Pill';
import Eyebrow from '../../components/Eyebrow';
import Button from '../../components/Button';
import { useWebSocket } from '../../hooks/useWebSocket';
import { api } from '../../api';
import RiskCockpit from '../../components/RiskCockpit';
import type { DaytradeLive, Position } from '../../types';

// ── Sub-tab config ──────────────────────────────────────────────
const SUBTABS = [
  { id: 'cockpit',  label: '駕駛艙',    path: '/daytrade' },
  { id: 'chart',    label: 'K 線標記',   path: '/daytrade/chart' },
  { id: 'order',    label: '下單流程',   path: '/daytrade/order' },
  { id: 'executed', label: '已執行',     path: '/daytrade/executed' },
  { id: 'threads',  label: '策略執行緒', path: '/daytrade/threads' },
  { id: 'risk',     label: '風控',       path: '/daytrade/risk' },
];

// ── Mock data ───────────────────────────────────────────────────
const MOCK: DaytradeLive = {
  countdown_seconds: 9762, // ~2h42m
  force_close_at: '13:25:00',
  monitoring_count: 5,
  closed_count: 2,
  unrealized_pnl: 8420,
  realized_pnl: 3920,
  net_pnl: 12340,
  net_value: 512340,
  positions: [
    {
      code: '2330', name: '台積電', sector: '半導體',
      side: 'buy',
      entry_price: 960, last_price: 975, quantity: 1, lot: 'common',
      cost: 960000, market_value: 975000,
      pnl: 15000, pnl_pct: 1.56,
      target_price: 1010, stop_loss_price: 940,
      distance_to_tp_pct: 3.59, distance_to_sl_pct: 3.59,
      thread_state: 'monitoring', confidence: 0.86,
      opened_at: '09:15:00',
    },
    {
      code: '2454', name: '聯發科', sector: '半導體',
      side: 'buy',
      entry_price: 1200, last_price: 1188, quantity: 1, lot: 'common',
      cost: 1200000, market_value: 1188000,
      pnl: -12000, pnl_pct: -1.0,
      target_price: 1260, stop_loss_price: 1170,
      distance_to_tp_pct: 6.06, distance_to_sl_pct: 1.52,
      thread_state: 'monitoring', confidence: 0.74,
      opened_at: '09:32:00',
    },
    {
      code: '6505', name: '台塑化', sector: '石化',
      side: 'buy',
      entry_price: 82, last_price: 84.5, quantity: 5000, lot: 'intraday_odd',
      cost: 410000, market_value: 422500,
      pnl: 12500, pnl_pct: 3.05,
      target_price: 87, stop_loss_price: 79,
      distance_to_tp_pct: 2.96, distance_to_sl_pct: 6.51,
      thread_state: 'monitoring', confidence: 0.79,
      opened_at: '10:05:00',
    },
  ],
  alerts: [
    {
      id: 'a1', time: '10:41:55', level: 'high', code: '2454', name: '聯發科',
      text: '接近停損價 NT$1,170，目前距離 1.52%',
      kind: 'stop_warn', resolved: false, source: 'monitor_agent', telegram_sent: true,
    },
    {
      id: 'a2', time: '10:38:12', level: 'med', code: '2330', name: '台積電',
      text: '達到 MA20 支撐確認，持續監控',
      kind: 'note', resolved: false, source: 'monitor_agent', telegram_sent: false,
    },
    {
      id: 'a3', time: '10:22:05', level: 'low', code: '6505', name: '台塑化',
      text: 'RSI 進入超買區間 (72.4)',
      kind: 'note', resolved: true, source: 'monitor_agent', telegram_sent: false,
    },
    {
      id: 'a4', time: '10:15:30', level: 'med', code: '2330', name: '台積電',
      text: '成交量突破均量 1.8x',
      kind: 'note', resolved: true, source: 'monitor_agent', telegram_sent: false,
    },
    {
      id: 'a5', time: '09:45:12', level: 'high', code: '3008', name: '大立光',
      text: '停損觸發，已執行平倉',
      kind: 'stop_loss', resolved: true, source: 'executor', telegram_sent: true,
    },
    {
      id: 'a6', time: '09:30:00', level: 'low', code: 'SYSTEM', name: '系統',
      text: '開盤監控啟動，5 檔策略執行緒上線',
      kind: 'note', resolved: true, source: 'system', telegram_sent: false,
    },
  ],
  threads: [
    {
      code: '2330', name: '台積電', state: 'monitoring',
      last_tick_at: '10:41:58', age_seconds: 5218,
      target_price: 1010, stop_loss_price: 940,
      distance_label: 'TP +3.59% / SL -3.59%',
      poll_count: 312, alert_count: 2,
    },
    {
      code: '2454', name: '聯發科', state: 'monitoring',
      last_tick_at: '10:41:58', age_seconds: 4178,
      target_price: 1260, stop_loss_price: 1170,
      distance_label: 'TP +6.06% / SL -1.52%',
      poll_count: 250, alert_count: 1,
    },
    {
      code: '6505', name: '台塑化', state: 'monitoring',
      last_tick_at: '10:41:58', age_seconds: 2218,
      target_price: 87, stop_loss_price: 79,
      distance_label: 'TP +2.96% / SL -6.51%',
      poll_count: 132, alert_count: 0,
    },
    {
      code: '3008', name: '大立光', state: 'closed_sl',
      last_tick_at: '09:45:00', age_seconds: 2700,
      target_price: 2800, stop_loss_price: 2680,
      distance_label: '已停損出場',
      poll_count: 178, alert_count: 3,
    },
    {
      code: '2317', name: '鴻海', state: 'closed_tp',
      last_tick_at: '09:55:00', age_seconds: 1500,
      target_price: 185, stop_loss_price: 176,
      distance_label: '已達停利出場',
      poll_count: 90, alert_count: 1,
    },
  ],
  risk: {
    budget: 1200000, used: 836500, free: 363500,
    utilization: 0.697,
    intraday_pnl: 12340, intraday_pnl_pct: 0.0148,
    daily_max_dd_limit: -60000,
    sector_allocation: [
      { name: '半導體', ratio: 0.58, limit: 0.50, value: 484850 },
      { name: '石化',   ratio: 0.18, limit: 0.40, value: 150570 },
      { name: '電子',   ratio: 0.14, limit: 0.40, value: 117110 },
      { name: '金融',   ratio: 0.10, limit: 0.30, value: 83650  },
    ],
    blacklist: ['3231', '2498', '6669'],
    single_max: { value: 484850, ratio: 0.58, limit: 0.50, ok: false },
  },
  next_poll_in_seconds: 8,
};

// ── Number formatters ───────────────────────────────────────────
function fmtPrice(n: number) {
  return n.toLocaleString('zh-TW');
}
function fmtPnl(n: number) {
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toLocaleString('zh-TW')}`;
}

// ── Position table ──────────────────────────────────────────────
function PositionTable({
  positions,
  onRowClick,
}: {
  positions: Position[];
  onRowClick: (code: string) => void;
}) {
  const stateLabel: Record<string, string> = {
    monitoring: '監控中',
    pending: '待命',
    closed_tp: '止盈',
    closed_sl: '停損',
    closed_force: '強平',
    rejected: '拒絕',
  };
  const stateColor: Record<string, string> = {
    monitoring: 'var(--gold)',
    pending: 'var(--muted)',
    closed_tp: 'var(--down)',
    closed_sl: 'var(--up)',
    closed_force: 'var(--up)',
    rejected: 'var(--muted-2)',
  };
  const stateBg: Record<string, string> = {
    monitoring: 'var(--gold-soft)',
    pending: 'transparent',
    closed_tp: 'var(--down-soft)',
    closed_sl: 'var(--up-soft)',
    closed_force: 'var(--up-soft)',
    rejected: 'transparent',
  };

  return (
    <div style={{ overflow: 'auto', flex: 1 }}>
      {/* Table header */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '120px 64px 80px 80px 60px 88px 90px 1fr 80px',
        borderBottom: '1px solid var(--hair)',
        background: 'var(--surface-2)',
        fontSize: 10,
        fontFamily: 'var(--font-mono)',
        color: 'var(--muted)',
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        flexShrink: 0,
      }}>
        {['標的', '單位', '進場', '現價', '數量', '市值', '損益', '停利/停損', '狀態'].map((h, i) => (
          <div key={i} style={{ padding: '6px 8px', borderRight: '1px solid var(--hair)' }}>{h}</div>
        ))}
      </div>

      {/* Rows */}
      {positions.map((p, idx) => {
        const isUp = p.pnl >= 0;
        return (
          <div
            key={p.code}
            onClick={() => onRowClick(p.code)}
            style={{
              display: 'grid',
              gridTemplateColumns: '120px 64px 80px 80px 60px 88px 90px 1fr 80px',
              borderBottom: '1px solid var(--hair)',
              background: idx % 2 === 1 ? 'var(--surface-2)' : 'var(--surface)',
              cursor: 'pointer',
              minHeight: 44,
              alignItems: 'center',
            }}
          >
            {/* 標的 */}
            <div style={{ padding: '6px 8px', borderRight: '1px solid var(--hair)' }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: 12, color: 'var(--ink)' }}>{p.code}</div>
              <div style={{ fontSize: 10, color: 'var(--muted)' }}>{p.name}</div>
            </div>

            {/* 單位 */}
            <div style={{ padding: '6px 8px', borderRight: '1px solid var(--hair)' }}>
              <Pill
                color={p.lot === 'common' ? 'var(--ink)' : 'var(--gold)'}
                bg={p.lot === 'common' ? 'transparent' : 'var(--gold-soft)'}
                border={p.lot === 'common' ? 'var(--hair-2)' : 'var(--gold)'}
                size={10}
              >
                {p.lot === 'common' ? '整張' : '零股'}
              </Pill>
            </div>

            {/* 進場 */}
            <div style={{ padding: '6px 8px', borderRight: '1px solid var(--hair)', fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1', fontSize: 11, color: 'var(--ink-2)' }}>
              {fmtPrice(p.entry_price)}
            </div>

            {/* 現價 */}
            <div style={{ padding: '6px 8px', borderRight: '1px solid var(--hair)', fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1', fontSize: 12, fontWeight: 500, color: isUp ? 'var(--up)' : 'var(--down)' }}>
              {fmtPrice(p.last_price)}
            </div>

            {/* 數量 */}
            <div style={{ padding: '6px 8px', borderRight: '1px solid var(--hair)', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ink-2)', textAlign: 'right' }}>
              {p.lot === 'common' ? `${p.quantity}張` : `${p.quantity.toLocaleString()}股`}
            </div>

            {/* 市值 */}
            <div style={{ padding: '6px 8px', borderRight: '1px solid var(--hair)', fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1', fontSize: 11, color: 'var(--ink-2)', textAlign: 'right' }}>
              {fmtPrice(p.market_value)}
            </div>

            {/* 損益雙行 */}
            <div style={{ padding: '6px 8px', borderRight: '1px solid var(--hair)' }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1', fontSize: 12, fontWeight: 500, color: isUp ? 'var(--up)' : 'var(--down)' }}>
                {fmtPnl(p.pnl)}
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1', fontSize: 10, color: isUp ? 'var(--up)' : 'var(--down)' }}>
                {isUp ? '+' : ''}{p.pnl_pct.toFixed(2)}%
              </div>
            </div>

            {/* DistRangeBar */}
            <div style={{ padding: '4px 8px', borderRight: '1px solid var(--hair)' }}>
              <DistRangeBar
                slPct={-p.distance_to_sl_pct}
                tpPct={p.distance_to_tp_pct}
                currentIsSafe={p.pnl >= 0}
              />
            </div>

            {/* 狀態 */}
            <div style={{ padding: '6px 8px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Pill
                color={stateColor[p.thread_state] ?? 'var(--muted)'}
                bg={stateBg[p.thread_state] ?? 'transparent'}
                border={stateColor[p.thread_state] ?? 'var(--hair)'}
                size={10}
              >
                {stateLabel[p.thread_state] ?? p.thread_state}
              </Pill>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Main ────────────────────────────────────────────────────────
export default function Cockpit() {
  const navigate = useNavigate();
  const [liveData, setLiveData] = useState<DaytradeLive>(MOCK);
  const [showCloseAllModal, setShowCloseAllModal] = useState(false);
  const [closingAll, setClosingAll] = useState(false);

  // WebSocket live feed
  const { data: wsData } = useWebSocket<DaytradeLive>('/ws/daytrade');

  // Merge WS data
  useEffect(() => {
    if (wsData) setLiveData(wsData);
  }, [wsData]);

  // Fetch initial data
  useEffect(() => {
    api.getDaytradeLive().then(setLiveData).catch(() => {/* use mock */});
  }, []);

  const handleCloseAll = useCallback(async () => {
    setClosingAll(true);
    try {
      await api.closeAll();
      setShowCloseAllModal(false);
    } catch {
      // handle error
    } finally {
      setClosingAll(false);
    }
  }, []);

  const { countdown_seconds, monitoring_count, closed_count } = liveData;
  const slTriggered = liveData.alerts.filter((a) => a.kind === 'stop_loss').length;

  return (
    <AppChrome title="AI 當沖 · 駕駛艙" eyebrow="04.1">
      {/* Sub-tabs */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        height: 38,
        borderBottom: '1px solid var(--hair)',
        background: 'var(--surface)',
        padding: '0 14px',
        gap: 0,
        flexShrink: 0,
      }}>
        {SUBTABS.map((tab) => {
          const active = tab.id === 'cockpit';
          return (
            <button
              key={tab.id}
              onClick={() => navigate(tab.path)}
              style={{
                height: 38,
                padding: '0 14px',
                fontSize: 12,
                fontWeight: active ? 500 : 400,
                color: active ? 'var(--ink)' : 'var(--muted)',
                background: 'transparent',
                border: 'none',
                borderBottom: active ? '2px solid var(--ink)' : '2px solid transparent',
                cursor: 'pointer',
                fontFamily: 'inherit',
                whiteSpace: 'nowrap',
              }}
            >
              {tab.label}
            </button>
          );
        })}

        <div style={{ flex: 1 }} />

        {/* Close all button */}
        <Button
          variant="danger"
          size="small"
          onClick={() => setShowCloseAllModal(true)}
        >
          全部平倉
        </Button>
      </div>

      {/* CountdownBar */}
      <CountdownBar
        seconds={countdown_seconds}
        monitoringCount={monitoring_count}
        closedCount={closed_count}
        slTriggered={slTriggered}
      />

      {/* Main 2×2 grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gridTemplateRows: '1fr 1fr',
        gap: 1,
        background: 'var(--hair)',
        flex: 1,
        height: 'calc(100vh - 44px - 22px - 38px - 68px)',
        overflow: 'hidden',
      }}>
        {/* TL: 持倉表 */}
        <div style={{ background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--hair)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
            <Eyebrow label="當前持倉" right={
              <Pill color="var(--gold)" bg="var(--gold-soft)" border="var(--gold)" size={10}>
                {liveData.positions.length} 檔
              </Pill>
            } />
          </div>
          <PositionTable
            positions={liveData.positions}
            onRowClick={(code) => navigate(`/daytrade/${code}/chart`)}
          />
        </div>

        {/* TR: 警報串流 */}
        <div style={{ background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--hair)', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <Eyebrow label="警報串流" right={
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                fontSize: 10, fontFamily: 'var(--font-mono)',
                color: 'var(--up)', letterSpacing: '0.04em',
              }}>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%', background: 'var(--up)',
                  animation: 'pulse 1.5s ease-in-out infinite',
                }} />
                LIVE
              </span>
            } />
          </div>
          <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }`}</style>
          <div style={{ overflowY: 'auto', flex: 1 }}>
            {liveData.alerts.map((alert) => (
              <AlertRow
                key={alert.id}
                alert={alert}
                onClick={() => navigate(`/daytrade/${alert.code}/chart`)}
              />
            ))}
          </div>
        </div>

        {/* BL: 策略執行緒 */}
        <div style={{ background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--hair)', flexShrink: 0 }}>
            <Eyebrow label="策略執行緒" right={
              <Pill color="var(--muted)" bg="transparent" border="var(--hair)" size={10}>
                {liveData.threads.length} 緒
              </Pill>
            } />
          </div>
          <div style={{ overflowY: 'auto', flex: 1 }}>
            {liveData.threads.map((thread) => (
              <ThreadRow key={thread.code} thread={thread} />
            ))}
          </div>
        </div>

        {/* BR: 風控儀表 */}
        <div style={{ background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--hair)', flexShrink: 0 }}>
            <Eyebrow label="風控儀表" right={
              <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: liveData.risk.single_max.ok ? 'var(--down)' : 'var(--up)' }}>
                {liveData.risk.single_max.ok ? '全部合規' : '超限警告'}
              </span>
            } />
          </div>
          <div style={{ overflowY: 'auto', flex: 1 }}>
            <RiskCockpit risk={liveData.risk} />
          </div>
        </div>
      </div>

      {/* Close all modal */}
      {showCloseAllModal && (
        <div style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.5)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 1000,
        }}>
          <div style={{
            background: 'var(--surface)',
            border: '1px solid var(--hair)',
            padding: 24,
            minWidth: 360,
          }}>
            <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--ink)', marginBottom: 8 }}>
              確認全部平倉
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
              此操作將立即以市價平倉所有 {liveData.positions.length} 個持倉，此操作不可撤銷。
            </div>
            <div style={{ fontSize: 11, color: 'var(--up)', marginBottom: 20, fontFamily: 'var(--font-mono)' }}>
              預估成交市值：NT${fmtPrice(liveData.positions.reduce((s, p) => s + p.market_value, 0))}
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Button variant="default" onClick={() => setShowCloseAllModal(false)}>
                取消
              </Button>
              <Button variant="danger" onClick={handleCloseAll} disabled={closingAll}>
                {closingAll ? '平倉中…' : '確認全部平倉'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </AppChrome>
  );
}
