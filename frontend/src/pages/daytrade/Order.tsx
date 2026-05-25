// 04.3 AI 當沖 · 下單流程 — Wave 3-H
import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import AppChrome from '../../components/AppChrome';
import GuardRow from '../../components/GuardRow';
import TelegramMsg from '../../components/TelegramMsg';
import Eyebrow from '../../components/Eyebrow';
import Pill from '../../components/Pill';
import Button from '../../components/Button';
import { api } from '../../api';
import type { OrderTicket, OrderResult, Side, LotType, RiskCheck } from '../../types';

// ── Mock data ───────────────────────────────────────────────────
const MOCK_CHECKS: RiskCheck[] = [
  { key: '單筆上限', sub: '≤ NT$200,000', status: 'pass', detail: 'NT$113,500 ✓' },
  { key: '資金充足', sub: '可用 NT$363,600', status: 'pass', detail: '使用率 31.2%' },
  { key: '板塊集中度', sub: '半導體 ≤ 50%', status: 'warn', detail: '目前 48.5% (近限)' },
  { key: '信心門檻', sub: '≥ 0.75', status: 'pass', detail: '0.86 ✓' },
  { key: '黑名單', sub: '標的未列入', status: 'pass', detail: '未在黑名單' },
  { key: '13:25 距離', sub: '≥ 30 分鐘', status: 'pass', detail: '剩餘 2h42m ✓' },
];

const MOCK_TICKET: OrderTicket = {
  code: '2330',
  name: '台積電',
  last_price: 975,
  side: 'buy',
  lot: 'common',
  price_type: 'LMT',
  price: 975,
  quantity: 1,
  amount: 975000,
  target_price: 1010,
  stop_loss_price: 940,
  source: {
    type: 'ai',
    run_id: 'RUN-20260525-0830',
    confidence: 0.86,
    reason: '突破 MA20 並伴隨量能放大，RSI 60 多頭格局，板塊資金持續流入',
    model: 'claude-sonnet-4-6',
  },
  risk_checks: MOCK_CHECKS,
  mode: 'simulation',
  dry_run_preview: `api.place_order(
  api_key   = "***",
  contract  = contracts.Stocks["2330"],
  order     = Order(
    action         = Action.Buy,
    price          = 975,
    quantity       = 1,
    price_type     = StockPriceType.LimitPrice,
    order_type     = OrderType.ROD,
    first_sell     = False,
  )
)
# DRY RUN — 模擬模式不送真實委託
# 預期回傳: {"order_id": "mock-xxx", "status": "pending"}`,
};

// ── Telegram mock messages ──────────────────────────────────────
interface TgMsg {
  role: 'bot' | 'user';
  content: string;
  time: string;
  isWarning?: boolean;
  buttons?: { label: string }[];
}

const MOCK_TG_MSGS: TgMsg[] = [
  {
    role: 'bot',
    content: '📊 AI 盤前選股完成\n\n🎯 2330 台積電\n💰 進場：NT$975\n🎯 目標：NT$1,010 (+3.59%)\n🛡 停損：NT$940 (-3.59%)\n\n信心指數：0.86（高）\n理由：突破 MA20，量能放大',
    time: '08:30:05',
    buttons: [{ label: '✅ 批准' }, { label: '❌ 跳過' }, { label: '✏️ 調整' }],
  },
  {
    role: 'user',
    content: '✅ 批准',
    time: '08:31:22',
  },
  {
    role: 'bot',
    content: '✅ 委託已送出\n\n成交：NT$975 × 1 張\n市值：NT$975,000\n訂單編號：mock-20260525-001',
    time: '09:15:44',
  },
  {
    role: 'bot',
    content: '⚠️ 風險提醒\n\n2330 台積電 接近停損\n現價：NT$952（-2.36%）\n停損價：NT$940（距 -1.26%）\n\n建議操作：持倉觀察 / 手動平倉',
    time: '10:42:15',
    isWarning: true,
    buttons: [{ label: '手動平倉' }, { label: '調整停損' }, { label: '忽略' }],
  },
];

// ── Helpers ─────────────────────────────────────────────────────
function fmtMoney(n: number) {
  return `NT$${n.toLocaleString('zh-TW')}`;
}

// ── 6-step flow ──────────────────────────────────────────────────
const STEPS = ['送出委託', 'Shioaji 接單', '撮合成交', '寫入 DB', '啟動 Monitor', 'Telegram 通知'];
type StepStatus = 'idle' | 'running' | 'done' | 'error';

function StepGrid({ status }: { status: StepStatus[] }) {
  const colors: Record<StepStatus, string> = {
    idle: 'var(--muted-2)',
    running: 'var(--gold)',
    done: 'var(--down)',
    error: 'var(--up)',
  };
  const icons: Record<StepStatus, string> = {
    idle: '○',
    running: '◎',
    done: '✓',
    error: '✗',
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
      {STEPS.map((label, i) => {
        const st = status[i] ?? 'idle';
        return (
          <div key={i} style={{
            padding: '6px 8px',
            border: `1px solid ${st === 'idle' ? 'var(--hair)' : colors[st]}`,
            background: st === 'done' ? 'var(--down-soft)' : st === 'running' ? 'var(--gold-soft)' : st === 'error' ? 'var(--up-soft)' : 'transparent',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: colors[st] }}>
              {icons[st]}
            </span>
            <span style={{ fontSize: 11, color: 'var(--ink-2)' }}>{label}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── Submit confirm modal ─────────────────────────────────────────
function SubmitModal({
  ticket,
  onClose,
  onConfirm,
}: {
  ticket: OrderTicket;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);

  const handleConfirm = async () => {
    setSubmitting(true);
    try { await onConfirm(); } finally { setSubmitting(false); }
  };

  const isBuy = ticket.side === 'buy';
  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 1000,
    }}>
      <div style={{
        background: 'var(--surface)',
        border: `1px solid ${isBuy ? 'var(--up)' : 'var(--down)'}`,
        padding: 24, minWidth: 380,
      }}>
        <div style={{ fontSize: 13, fontWeight: 500, color: isBuy ? 'var(--up)' : 'var(--down)', marginBottom: 10 }}>
          確認委託 — {ticket.code} {ticket.name}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12, fontSize: 12 }}>
          <div><span style={{ color: 'var(--muted)' }}>方向</span> <span style={{ fontFamily: 'var(--font-mono)', color: isBuy ? 'var(--up)' : 'var(--down)' }}>{isBuy ? 'BUY' : 'SELL'}</span></div>
          <div><span style={{ color: 'var(--muted)' }}>委託類型</span> <span style={{ fontFamily: 'var(--font-mono)' }}>{ticket.lot === 'common' ? '整張' : '零股'}</span></div>
          <div><span style={{ color: 'var(--muted)' }}>委託價</span> <span style={{ fontFamily: 'var(--font-mono)' }}>{ticket.price.toLocaleString()}</span></div>
          <div><span style={{ color: 'var(--muted)' }}>數量</span> <span style={{ fontFamily: 'var(--font-mono)' }}>{ticket.quantity}{ticket.lot === 'common' ? '張' : '股'}</span></div>
          <div><span style={{ color: 'var(--muted)' }}>委託金額</span> <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 500 }}>{fmtMoney(ticket.amount)}</span></div>
          <div><span style={{ color: 'var(--muted)' }}>模式</span> <span style={{ fontFamily: 'var(--font-mono)', color: ticket.mode === 'simulation' ? 'var(--gold)' : 'var(--up)' }}>{ticket.mode === 'simulation' ? '模擬' : '真實'}</span></div>
        </div>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 16, padding: '8px 10px', background: 'var(--gold-soft)', border: '1px solid var(--gold)' }}>
          送出後將透過 Telegram 進行二次確認，請於 60 秒內回應。
        </div>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <Button variant="default" onClick={onClose}>取消</Button>
          <Button
            variant={isBuy ? 'danger' : 'default'}
            disabled={submitting}
            onClick={handleConfirm}
            style={!isBuy ? { background: 'var(--down)', color: '#fff', border: 'none' } : undefined}
          >
            {submitting ? '送出中…' : `確認 ${isBuy ? '買進' : '賣出'} · ${fmtMoney(ticket.amount)}`}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Main ────────────────────────────────────────────────────────
export default function Order() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const initCode = searchParams.get('code') ?? '2330';

  // Form state
  const [code] = useState(initCode);
  const [side, setSide] = useState<Side>('buy');
  const [lot, setLot] = useState<LotType>('common');
  const [priceType, setPriceType] = useState<'LMT' | 'MKT'>('LMT');
  const [price, setPrice] = useState(975);
  const [qty, setQty] = useState(1);
  const [tp, setTp] = useState(1010);
  const [sl, setSl] = useState(940);

  // Preview
  const [ticket, setTicket] = useState<OrderTicket>(MOCK_TICKET);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Step status
  const [stepStatus, setStepStatus] = useState<StepStatus[]>(STEPS.map(() => 'idle'));

  // Modals
  const [showSubmit, setShowSubmit] = useState(false);
  const [_result, setResult] = useState<OrderResult | null>(null);

  // Computed amount
  const amount = lot === 'common' ? price * qty * 1000 : price * qty;

  // Debounced preview fetch
  const fetchPreview = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const t = await api.previewOrder({
          code, side, lot, price_type: priceType, price, quantity: qty,
          target_price: tp, stop_loss_price: sl,
        });
        setTicket(t);
      } catch {
        // keep mock
      } finally {
        setLoading(false);
      }
    }, 500);
  }, [code, side, lot, priceType, price, qty, tp, sl]);

  useEffect(() => { fetchPreview(); }, [side, lot, priceType, price, qty]);

  const handleSubmit = useCallback(async () => {
    // Animate steps
    for (let i = 0; i < STEPS.length; i++) {
      setStepStatus((prev) => prev.map((s, idx) => idx === i ? 'running' : s));
      await new Promise((r) => setTimeout(r, 600));
      setStepStatus((prev) => prev.map((s, idx) => idx === i ? 'done' : s));
    }
    try {
      const r = await api.submitOrder(ticket);
      setResult(r);
    } catch {
      setResult({ order_id: 'mock-sim-001', status: 'submitted' });
    }
    setShowSubmit(false);
    navigate('/daytrade');
  }, [ticket, navigate]);

  const passCount = ticket.risk_checks.filter((c) => c.status === 'pass').length;
  const isBuy = side === 'buy';

  return (
    <AppChrome title="下單流程" eyebrow="04.3">
      {/* 3-column layout */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr 1fr',
        gap: 1,
        background: 'var(--hair)',
        height: 'calc(100vh - 44px - 22px)',
        overflow: 'hidden',
      }}>

        {/* ── Left column: 下單委託 ────────────── */}
        <div style={{ background: 'var(--surface)', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--hair)', flexShrink: 0 }}>
            <Eyebrow label="下單委託" right={
              <Pill color="var(--gold)" bg="var(--gold-soft)" border="var(--gold)" size={10}>AI 預填</Pill>
            } />
          </div>

          <div style={{ padding: '14px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* 標的卡 */}
            <div style={{ padding: '10px 12px', border: '1px solid var(--hair)', background: 'var(--surface-2)' }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, fontSize: 16, color: 'var(--ink)' }}>{code}</span>
                <span style={{ fontSize: 12, color: 'var(--muted)' }}>台積電</span>
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1', fontSize: 18, fontWeight: 500, color: 'var(--ink)', marginTop: 2 }}>
                {ticket.last_price.toLocaleString()}
                <span style={{ fontSize: 12, color: 'var(--muted)', marginLeft: 6 }}>現價</span>
              </div>
            </div>

            {/* 買/賣 toggle */}
            <div>
              <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', marginBottom: 6, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                方向
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
                {(['buy', 'sell'] as Side[]).map((s) => (
                  <button
                    key={s}
                    onClick={() => setSide(s)}
                    style={{
                      height: 36, fontSize: 13, fontWeight: 500,
                      fontFamily: 'inherit', cursor: 'pointer',
                      background: side === s
                        ? (s === 'buy' ? 'var(--up)' : 'var(--down)')
                        : 'var(--surface)',
                      color: side === s ? '#fff' : 'var(--muted)',
                      border: `1px solid ${side === s ? (s === 'buy' ? 'var(--up)' : 'var(--down)') : 'var(--hair-2)'}`,
                      borderRadius: 2,
                    }}
                  >
                    {s === 'buy' ? '買進 BUY' : '賣出 SELL'}
                  </button>
                ))}
              </div>
            </div>

            {/* 整張/零股 radio cards */}
            <div>
              <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', marginBottom: 6, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                委託類型
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
                {([['common', '整張', 'Common'] as const, ['intraday_odd', '零股', 'IntradayOdd'] as const]).map(([val, label, sub]) => (
                  <button
                    key={val}
                    onClick={() => setLot(val)}
                    style={{
                      padding: '8px 10px', textAlign: 'left',
                      background: 'var(--surface)', cursor: 'pointer',
                      border: lot === val ? '2px solid var(--ink)' : '1px solid var(--hair-2)',
                      fontFamily: 'inherit',
                    }}
                  >
                    <div style={{ fontSize: 12, fontWeight: lot === val ? 500 : 400, color: 'var(--ink)' }}>{label}</div>
                    <div style={{ fontSize: 10, color: 'var(--muted)' }}>{sub}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* 價格類別 + 限價 雙欄 */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div>
                <label style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', display: 'block', marginBottom: 4, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                  價格類別
                </label>
                <select
                  value={priceType}
                  onChange={(e) => setPriceType(e.target.value as 'LMT' | 'MKT')}
                  style={{
                    width: '100%', height: 38, padding: '0 8px',
                    fontFamily: 'var(--font-mono)', fontSize: 12,
                    border: '1px solid var(--hair-2)', background: 'var(--panel)',
                    color: 'var(--ink)', cursor: 'pointer',
                  }}
                >
                  <option value="LMT">限價 LMT</option>
                  <option value="MKT">市價 MKT</option>
                </select>
              </div>
              <div>
                <label style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', display: 'block', marginBottom: 4, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                  限價
                </label>
                <input
                  type="number"
                  value={price}
                  onChange={(e) => setPrice(parseFloat(e.target.value) || 0)}
                  disabled={priceType === 'MKT'}
                  style={{
                    width: '100%', height: 38, padding: '0 8px',
                    fontFamily: 'var(--font-mono)', fontSize: 13,
                    border: '1px solid var(--hair-2)', background: priceType === 'MKT' ? 'var(--surface-2)' : 'var(--panel)',
                    color: 'var(--ink)', boxSizing: 'border-box',
                  }}
                  step={0.5}
                />
              </div>
            </div>

            {/* 數量 + 金額 */}
            <div>
              <label style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', display: 'block', marginBottom: 4, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                數量（{lot === 'common' ? '張' : '股'}）
              </label>
              <input
                type="number"
                value={qty}
                min={1}
                onChange={(e) => setQty(parseInt(e.target.value) || 1)}
                style={{
                  width: '100%', height: 38, padding: '0 8px',
                  fontFamily: 'var(--font-mono)', fontSize: 13,
                  border: '1px solid var(--hair-2)', background: 'var(--panel)',
                  color: 'var(--ink)', boxSizing: 'border-box',
                }}
              />
              <div style={{
                marginTop: 4, fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1',
                fontSize: 13, fontWeight: 500, color: isBuy ? 'var(--up)' : 'var(--down)',
              }}>
                委託金額：{fmtMoney(amount)}
              </div>
            </div>

            {/* 停利/停損 tile 並排 */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div style={{ padding: '8px 10px', background: 'var(--up-soft)', border: '1px solid var(--up)' }}>
                <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--up)', marginBottom: 4, letterSpacing: '0.1em' }}>停利 TP</div>
                <input
                  type="number"
                  value={tp}
                  onChange={(e) => setTp(parseFloat(e.target.value) || 0)}
                  style={{
                    width: '100%', height: 30, padding: '0 6px',
                    fontFamily: 'var(--font-mono)', fontSize: 13,
                    border: '1px solid var(--up)', background: 'var(--panel)',
                    color: 'var(--up)', boxSizing: 'border-box',
                  }}
                  step={0.5}
                />
                <div style={{ marginTop: 3, fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--up)' }}>
                  +{price > 0 ? (((tp - price) / price) * 100).toFixed(2) : '—'}%
                </div>
              </div>
              <div style={{ padding: '8px 10px', background: 'var(--down-soft)', border: '1px solid var(--down)' }}>
                <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--down)', marginBottom: 4, letterSpacing: '0.1em' }}>停損 SL</div>
                <input
                  type="number"
                  value={sl}
                  onChange={(e) => setSl(parseFloat(e.target.value) || 0)}
                  style={{
                    width: '100%', height: 30, padding: '0 6px',
                    fontFamily: 'var(--font-mono)', fontSize: 13,
                    border: '1px solid var(--down)', background: 'var(--panel)',
                    color: 'var(--down)', boxSizing: 'border-box',
                  }}
                  step={0.5}
                />
                <div style={{ marginTop: 3, fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--down)' }}>
                  {price > 0 ? (((sl - price) / price) * 100).toFixed(2) : '—'}%
                </div>
              </div>
            </div>

            {/* AI 來源卡 */}
            {ticket.source.type === 'ai' && (
              <div style={{ padding: '10px 12px', background: 'var(--gold-soft)', border: '1px solid var(--gold)' }}>
                <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--gold)', marginBottom: 6, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                  AI 來源 · {ticket.source.model ?? '—'}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>信心</span>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1',
                    fontSize: 16, fontWeight: 500, color: 'var(--gold)',
                  }}>
                    {((ticket.source.confidence ?? 0) * 100).toFixed(0)}%
                  </span>
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.5 }}>
                  {ticket.source.reason}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Middle column: 風控 + dry-run ─── */}
        <div style={{ background: 'var(--surface)', overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--hair)', flexShrink: 0 }}>
            <Eyebrow label="風控檢查 · 即時" right={
              <Pill
                color={passCount === ticket.risk_checks.length ? 'var(--down)' : 'var(--gold)'}
                bg={passCount === ticket.risk_checks.length ? 'var(--down-soft)' : 'var(--gold-soft)'}
                border={passCount === ticket.risk_checks.length ? 'var(--down)' : 'var(--gold)'}
                size={10}
              >
                {passCount}/{ticket.risk_checks.length} 通過
                {loading && ' ···'}
              </Pill>
            } />
          </div>

          {/* GuardRows */}
          <div style={{ flexShrink: 0 }}>
            {ticket.risk_checks.map((check, i) => (
              <GuardRow key={i} check={check} index={i} />
            ))}
          </div>

          {/* Step grid */}
          <div style={{ padding: '12px 14px', borderTop: '1px solid var(--hair)' }}>
            <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', marginBottom: 8, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
              下單後狀態預估
            </div>
            <StepGrid status={stepStatus} />
          </div>

          {/* Dry-run code block */}
          <div style={{ padding: '0 14px 14px' }}>
            <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--muted)', marginBottom: 6, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
              DRY-RUN PREVIEW
            </div>
            <pre style={{
              background: 'var(--ink-bg)', color: 'var(--dark-ink)',
              fontFamily: 'var(--font-mono)', fontSize: 10,
              padding: 12, margin: 0, overflowX: 'auto',
              lineHeight: 1.6, borderRadius: 0,
              border: '1px solid var(--dark-hair)',
            }}>
              {ticket.dry_run_preview}
            </pre>
          </div>
        </div>

        {/* ── Right column: Telegram + Actions ─── */}
        <div style={{ background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--hair)', flexShrink: 0 }}>
            <Eyebrow label="Telegram 鏡像" right={
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--muted-2)' }}>
                chat_id: @quant_ai_bot
              </span>
            } />
          </div>

          {/* Mock Telegram chat */}
          <div style={{
            flex: 1, overflowY: 'auto',
            background: '#e6dfd1',
            padding: '8px 0',
          }}>
            {MOCK_TG_MSGS.map((msg, i) => (
              <TelegramMsg
                key={i}
                role={msg.role}
                content={msg.content}
                time={msg.time}
                isWarning={msg.isWarning}
                buttons={msg.buttons}
              />
            ))}
          </div>

          {/* Sticky bottom actions */}
          <div style={{
            borderTop: '1px solid var(--hair)',
            padding: '12px 14px',
            background: 'var(--surface)',
            flexShrink: 0,
          }}>
            {/* Primary submit button */}
            <button
              onClick={() => setShowSubmit(true)}
              style={{
                width: '100%', height: 40,
                fontFamily: 'inherit', fontSize: 13, fontWeight: 500,
                cursor: 'pointer',
                background: isBuy ? 'var(--up)' : 'var(--down)',
                color: '#fff',
                border: 'none',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                marginBottom: 8,
              }}
            >
              送出委託 · {fmtMoney(amount)}
              <kbd style={{
                fontFamily: 'var(--font-mono)', fontSize: 10,
                background: 'rgba(255,255,255,0.2)',
                padding: '2px 5px', borderRadius: 2,
              }}>
                ⌘↵
              </kbd>
            </button>

            {/* Secondary buttons */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 8 }}>
              <Button size="small" variant="default">儲存草稿</Button>
              <Button size="small" variant="default">批准全部</Button>
            </div>

            {/* Fineprint */}
            <div style={{
              fontSize: 10, color: 'var(--muted-2)',
              lineHeight: 1.5, textAlign: 'center',
            }}>
              ▴ 確認前將二次寄送 Telegram · {ticket.mode === 'simulation' ? '模擬模式不會動用真實資金' : '真實模式將送出交易所'}
            </div>
          </div>
        </div>
      </div>

      {/* Submit modal */}
      {showSubmit && (
        <SubmitModal
          ticket={{ ...ticket, side, lot, price_type: priceType, price, quantity: qty, amount, target_price: tp, stop_loss_price: sl }}
          onClose={() => setShowSubmit(false)}
          onConfirm={handleSubmit}
        />
      )}
    </AppChrome>
  );
}
