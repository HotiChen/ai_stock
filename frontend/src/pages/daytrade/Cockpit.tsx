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
import { queryState } from '../../components/DataState';
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
  const [liveData, setLiveData] = useState<DaytradeLive | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [showCloseAllModal, setShowCloseAllModal] = useState(false);
  const [closingAll, setClosingAll] = useState(false);

  // WebSocket live feed
  const { data: wsData, wsError } = useWebSocket<DaytradeLive>('/ws/daytrade');

  // Merge WS data
  useEffect(() => {
    if (wsData) setLiveData(wsData);
  }, [wsData]);

  // Fetch initial data
  useEffect(() => {
    api.getDaytradeLive()
      .then(setLiveData)
      .catch(setLoadError)
      .finally(() => setLoading(false));
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

  const state = queryState({
    isLoading: loading,
    isError: !!loadError || (!liveData && !!wsError),
    error: loadError ?? (wsError ? new Error(wsError) : undefined),
    isEmpty: !liveData,
    what: '當沖實況',
    emptyDetail: '09:10 進場後才會有持倉；盤後 main.py 停止推送時這裡也會是空的。',
  });
  if (state) return <AppChrome title="AI 當沖 · 駕駛艙" eyebrow="04.1">{state}</AppChrome>;

  const live = liveData!;
  const { countdown_seconds, monitoring_count, closed_count } = live;
  const slTriggered = live.alerts.filter((a) => a.kind === 'stop_loss').length;

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
                {live.positions.length} 檔
              </Pill>
            } />
          </div>
          <PositionTable
            positions={live.positions}
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
            {live.alerts.map((alert) => (
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
                {live.threads.length} 緒
              </Pill>
            } />
          </div>
          <div style={{ overflowY: 'auto', flex: 1 }}>
            {live.threads.map((thread) => (
              <ThreadRow key={thread.code} thread={thread} />
            ))}
          </div>
        </div>

        {/* BR: 風控儀表 */}
        <div style={{ background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--hair)', flexShrink: 0 }}>
            <Eyebrow label="風控儀表" right={
              <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: live.risk.single_max.ok ? 'var(--down)' : 'var(--up)' }}>
                {live.risk.single_max.ok ? '全部合規' : '超限警告'}
              </span>
            } />
          </div>
          <div style={{ overflowY: 'auto', flex: 1 }}>
            <RiskCockpit risk={live.risk} />
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
              此操作將立即以市價平倉所有 {live.positions.length} 個持倉，此操作不可撤銷。
            </div>
            <div style={{ fontSize: 11, color: 'var(--up)', marginBottom: 20, fontFamily: 'var(--font-mono)' }}>
              預估成交市值：NT${fmtPrice(live.positions.reduce((s, p) => s + p.market_value, 0))}
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
