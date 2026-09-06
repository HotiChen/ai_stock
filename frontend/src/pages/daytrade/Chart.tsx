// 04.2 AI 當沖 · K 線標記 — Wave 3-H
import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import AppChrome from '../../components/AppChrome';
import Pill from '../../components/Pill';
import Eyebrow from '../../components/Eyebrow';
import Button from '../../components/Button';
import KChart, { markColor } from '../../components/KChart';
import { useWebSocket } from '../../hooks/useWebSocket';
import { api } from '../../api';
import { queryState } from '../../components/DataState';
import type { ChartView } from '../../types';

// ── Mock data ───────────────────────────────────────────────────
// ── Legend ──────────────────────────────────────────────────────
function ChartLegend({ ma20Valid: _ma20Valid }: { ma20Valid: boolean }) {
  return (
    <div style={{ display: 'flex', gap: 14, padding: '6px 8px', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', borderBottom: '1px solid var(--hair)', flexWrap: 'wrap' }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 12, height: 3, background: 'var(--gold)', display: 'inline-block' }} />
        MA20
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 12, height: 3, background: 'var(--up)', display: 'inline-block', borderTop: '1px dashed var(--up)' }} />
        TP
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 12, height: 3, background: 'var(--down)', display: 'inline-block', borderTop: '1px dashed var(--down)' }} />
        SL
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 10, height: 10, background: 'var(--up)', display: 'inline-block' }} />
        AI 進場
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 10, height: 10, background: 'var(--gold)', display: 'inline-block' }} />
        AI 警告
      </span>
    </div>
  );
}

// ── Adjust modal ─────────────────────────────────────────────────
function AdjustModal({
  type,
  code,
  current,
  onClose,
  onSave,
}: {
  type: 'tp' | 'sl';
  code: string;
  current: number;
  onClose: () => void;
  onSave: (value: number) => Promise<void>;
}) {
  const [val, setVal] = useState(String(current));
  const [saving, setSaving] = useState(false);
  const label = type === 'tp' ? '停利價' : '停損價';
  const color = type === 'tp' ? 'var(--up)' : 'var(--down)';

  const handleSave = async () => {
    const num = parseFloat(val);
    if (isNaN(num)) return;
    setSaving(true);
    try {
      await onSave(num);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(0,0,0,0.5)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000,
    }}>
      <div style={{
        background: 'var(--surface)',
        border: `1px solid ${color}`,
        padding: 24, minWidth: 300,
      }}>
        <div style={{ fontSize: 13, fontWeight: 500, color, marginBottom: 12 }}>
          調整 {code} {label}
        </div>
        <input
          type="number"
          value={val}
          onChange={(e) => setVal(e.target.value)}
          step={0.5}
          style={{
            width: '100%', height: 38, padding: '0 10px',
            fontFamily: 'var(--font-mono)', fontSize: 14,
            border: `1px solid ${color}`,
            background: 'var(--panel)', color: 'var(--ink)',
            boxSizing: 'border-box',
          }}
          autoFocus
        />
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 14 }}>
          <button onClick={onClose} style={{ padding: '6px 14px', fontSize: 12, cursor: 'pointer', background: 'var(--surface)', border: '1px solid var(--hair-2)', fontFamily: 'inherit' }}>
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{ padding: '6px 14px', fontSize: 12, cursor: 'pointer', background: color, color: '#fff', border: 'none', fontFamily: 'inherit' }}
          >
            {saving ? '儲存中…' : '確認'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main ────────────────────────────────────────────────────────
export default function ChartPage() {
  const { code = '2330' } = useParams<{ code: string }>();
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const [chartWidth, setChartWidth] = useState(700);
  const [chartData, setChartData] = useState<ChartView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [selectedMarkIdx, setSelectedMarkIdx] = useState<number | null>(null);
  const [adjustModal, setAdjustModal] = useState<'tp' | 'sl' | null>(null);
  const [showCloseConfirm, setShowCloseConfirm] = useState(false);
  const [closingPos, setClosingPos] = useState(false);

  // WS live updates
  const { data: wsData, wsError } = useWebSocket<ChartView>(`/ws/daytrade/${code}/chart`);
  useEffect(() => {
    if (wsData) setChartData(wsData);
  }, [wsData]);

  // Initial fetch
  useEffect(() => {
    setLoading(true);
    setLoadError(null);
    api.getChartData(code)
      .then(setChartData)
      .catch(setLoadError)
      .finally(() => setLoading(false));
  }, [code]);

  // Measure container width
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setChartWidth(el.clientWidth);
    });
    ro.observe(el);
    setChartWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const state = queryState({
    isLoading: loading,
    isError: !!loadError || (!chartData && !!wsError),
    error: loadError ?? (wsError ? new Error(wsError) : undefined),
    isEmpty: !chartData,
    what: `${code} 的 K 線`,
    emptyDetail: '需要當日 tick 資料；盤前或非交易日不會有。原本這裡畫的是程式產生的假 K 線。',
  });
  if (state) return <AppChrome title={`K 線 · ${code}`} eyebrow="04.2">{state}</AppChrome>;

  const chart = chartData!;
  const ticks = chart.ticks;
  const last = ticks[ticks.length - 1];
  const first = ticks[0];
  const lastClose = last?.close ?? 0;
  const openP = first?.open ?? 0;
  const highP = Math.max(...ticks.map((t) => t.high));
  const lowP = Math.min(...ticks.map((t) => t.low));
  const totalVol = ticks.reduce((s, t) => s + t.volume, 0);
  const changePct = openP > 0 ? ((lastClose - openP) / openP) * 100 : 0;
  const isUp = lastClose >= openP;

  // Mock position metadata
  const targetPrice = 1010;
  const stopLossPrice = 940;
  const monitoringMinutes = 87;
  const confidence = 0.86;
  const distTp = ((targetPrice - lastClose) / lastClose) * 100;
  const distSl = ((lastClose - stopLossPrice) / lastClose) * 100;

  void (selectedMarkIdx !== null
    ? chart.ai_marks.find((m) => m.index === selectedMarkIdx)
    : null);

  const handleAdjustTp = useCallback(async (value: number) => {
    await api.adjustTp(code, value);
  }, [code]);

  const handleAdjustSl = useCallback(async (value: number) => {
    await api.adjustSl(code, value);
  }, [code]);

  const handleClose = useCallback(async () => {
    setClosingPos(true);
    try {
      await api.closePick(code);
      navigate('/daytrade');
    } finally {
      setClosingPos(false);
    }
  }, [code, navigate]);

  return (
    <AppChrome title={`K 線標記 · ${code}`} eyebrow="04.2">
      {/* Identity strip */}
      <div style={{
        background: 'var(--surface)',
        borderBottom: '1px solid var(--hair)',
        padding: '10px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 20,
        flexShrink: 0,
      }}>
        {/* Left: code + name + price + OHLV */}
        <div style={{ minWidth: 240 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 2 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: 16, color: 'var(--ink)' }}>
              {code}
            </span>
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>台積電</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 4 }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1',
              fontSize: 22, fontWeight: 500,
              color: isUp ? 'var(--up)' : 'var(--down)',
            }}>
              {lastClose.toFixed(1)}
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: isUp ? 'var(--up)' : 'var(--down)' }}>
              {isUp ? '+' : ''}{changePct.toFixed(2)}%
            </span>
          </div>
          <div style={{ display: 'flex', gap: 10, fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted-2)' }}>
            <span>O {openP.toFixed(1)}</span>
            <span>H {highP.toFixed(1)}</span>
            <span>L {lowP.toFixed(1)}</span>
            <span>V {totalVol.toLocaleString()}</span>
          </div>
        </div>

        {/* Pills */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <Pill color="var(--gold)" bg="var(--gold-soft)" border="var(--gold)" size={10}>
            監控 {monitoringMinutes}m
          </Pill>
          <Pill
            color={confidence >= 0.75 ? 'var(--up)' : confidence >= 0.6 ? 'var(--gold)' : 'var(--muted)'}
            bg={confidence >= 0.75 ? 'var(--up-soft)' : confidence >= 0.6 ? 'var(--gold-soft)' : 'transparent'}
            border={confidence >= 0.75 ? 'var(--up)' : confidence >= 0.6 ? 'var(--gold)' : 'var(--hair)'}
            size={10}
          >
            AI {(confidence * 100).toFixed(0)}%
          </Pill>
          <Pill color="var(--down)" bg="var(--down-soft)" border="var(--down)" size={10}>
            距TP +{distTp.toFixed(2)}%
          </Pill>
          <Pill color="var(--up)" bg="var(--up-soft)" border="var(--up)" size={10}>
            距SL -{distSl.toFixed(2)}%
          </Pill>
        </div>

        <div style={{ flex: 1 }} />

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <Button size="small" onClick={() => setAdjustModal('tp')}>
            調整停利
          </Button>
          <Button size="small" onClick={() => setAdjustModal('sl')}>
            調整停損
          </Button>
          <Button size="small" variant="danger" onClick={() => setShowCloseConfirm(true)}>
            立即平倉
          </Button>
        </div>
      </div>

      {/* Main body: left chart + right column */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left: chart */}
        <div ref={containerRef} style={{ flex: 1, overflow: 'auto', background: 'var(--surface)', borderRight: '1px solid var(--hair)' }}>
          <ChartLegend ma20Valid={chart.ma20.some((v) => !isNaN(v))} />
          <KChart
            ticks={ticks}
            ma20={chart.ma20}
            rsi={chart.rsi}
            targetPrice={targetPrice}
            stopLossPrice={stopLossPrice}
            aiMarks={chart.ai_marks}
            selectedMarkIndex={selectedMarkIdx}
            onSelectMark={setSelectedMarkIdx}
            width={chartWidth}
          />
        </div>

        {/* Right column: 320px */}
        <div style={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--surface)' }}>
          {/* AI mark log */}
          <div style={{ borderBottom: '1px solid var(--hair)', padding: '8px 12px', flexShrink: 0 }}>
            <Eyebrow label="AI 進出場標記" right={
              <Pill color="var(--muted)" bg="transparent" border="var(--hair)" size={10}>
                {chart.ai_marks.length} 個
              </Pill>
            } />
          </div>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {chart.ai_marks.map((mark) => {
              const color = markColor[mark.kind] ?? 'var(--muted)';
              const isSelected = selectedMarkIdx === mark.index;
              return (
                <div
                  key={mark.index}
                  onClick={() => setSelectedMarkIdx(isSelected ? null : mark.index)}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 8,
                    padding: '8px 12px',
                    borderBottom: '1px solid var(--hair)',
                    borderLeft: `3px solid ${color}`,
                    background: isSelected ? 'var(--surface-2)' : 'transparent',
                    cursor: 'pointer',
                  }}
                >
                  {/* Icon box */}
                  <div style={{
                    width: 22, height: 22, background: color,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    flexShrink: 0,
                  }}>
                    <span style={{ fontSize: 8, color: '#fff', fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                      {mark.label}
                    </span>
                  </div>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                      <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--ink)' }}>
                        {mark.label} — {mark.kind.toUpperCase()}
                      </span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--muted)' }}>
                        {mark.time}
                      </span>
                    </div>
                    {mark.reasoning && (
                      <div style={{ fontSize: 10, color: 'var(--muted)', lineHeight: 1.5 }}>
                        {mark.reasoning}
                      </div>
                    )}
                    <div style={{ marginTop: 3 }}>
                      <Pill
                        color={mark.confidence >= 0.75 ? 'var(--up)' : mark.confidence >= 0.6 ? 'var(--gold)' : 'var(--muted)'}
                        bg="transparent"
                        border="var(--hair)"
                        size={10}
                      >
                        conf {(mark.confidence * 100).toFixed(0)}%
                      </Pill>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Next action suggestion */}
          {chart.next_action_suggestion && (
            <div style={{
              borderTop: '1px solid var(--hair)',
              borderLeft: '3px solid var(--up)',
              background: 'var(--surface)',
              padding: '12px 14px',
              flexShrink: 0,
            }}>
              <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 6 }}>
                建議下一步
              </div>
              <div style={{ fontSize: 12, color: 'var(--ink)', lineHeight: 1.6, marginBottom: 8 }}>
                {chart.next_action_suggestion.text}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <Pill
                  color={chart.next_action_suggestion.confidence >= 0.75 ? 'var(--up)' : 'var(--gold)'}
                  bg="transparent" border="var(--hair)" size={10}
                >
                  AI 信心 {(chart.next_action_suggestion.confidence * 100).toFixed(0)}%
                </Pill>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <Button
                  size="small"
                  variant="primary"
                  onClick={() => {
                    const s = chart.next_action_suggestion;
                    if (!s) return;
                    if (s.kind === 'adjust_tp' && s.suggested_value) api.adjustTp(code, s.suggested_value);
                    if (s.kind === 'adjust_sl' && s.suggested_value) api.adjustSl(code, s.suggested_value);
                  }}
                >
                  套用
                </Button>
                <Button size="small" variant="ghost">忽略</Button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Adjust modal */}
      {adjustModal && (
        <AdjustModal
          type={adjustModal}
          code={code}
          current={adjustModal === 'tp' ? targetPrice : stopLossPrice}
          onClose={() => setAdjustModal(null)}
          onSave={adjustModal === 'tp' ? handleAdjustTp : handleAdjustSl}
        />
      )}

      {/* Close confirm */}
      {showCloseConfirm && (
        <div style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.5)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          zIndex: 1000,
        }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--up)', padding: 24, minWidth: 320 }}>
            <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--up)', marginBottom: 8 }}>
              立即平倉 {code}
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 16 }}>
              將以市價立即平倉此持倉，操作不可撤銷，並發送 Telegram 通知。
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Button variant="default" onClick={() => setShowCloseConfirm(false)}>取消</Button>
              <Button variant="danger" disabled={closingPos} onClick={handleClose}>
                {closingPos ? '平倉中…' : '確認平倉'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </AppChrome>
  );
}
