// 03.3 AI 預測 · 推理過程 — Wave 3-G
import { useEffect, useState, useRef } from 'react';
import { useParams } from 'react-router-dom';
import AppChrome from '../../components/AppChrome';
import TraceStep from '../../components/TraceStep';
import ContribRow from '../../components/ContribRow';
import Card from '../../components/Card';
import { api } from '../../api';
import type { ReasoningTrace } from '../../types';

// ── Mock data ──────────────────────────────────────────────────────────────────
function mockReasoningTrace(code: string): ReasoningTrace {
  return {
    run_id: 'PRE-20260525-001',
    code,
    total_duration_ms: 18240,
    steps: [
      {
        phase: 'INPUT',
        t: '08:30:00',
        label: '接收盤前分析請求',
        body: `{ "code": "${code}", "date": "2026-05-25", "mode": "deep" }`,
        cost_ms: 12,
      },
      {
        phase: 'FETCH',
        t: '08:30:00',
        label: '拉取日K / 週K 歷史資料',
        body: `行情資料：240 日 OHLCV\n最新收盤：975 (+1.56%)\n前一日量：36,821 張`,
        cost_ms: 340,
      },
      {
        phase: 'FETCH',
        t: '08:30:01',
        label: '拉取法人籌碼 + 新聞',
        body: `外資：連買 5 日，淨買超 12,840 張\n投信：買超 3,200 張\n新聞：CoWoS 擴產，N2 良率超預期`,
        cost_ms: 820,
      },
      {
        phase: 'EVAL',
        t: '08:30:01',
        label: '計算技術指標',
        body: `RSI(14) = 62.4\nMACD = 金叉 (DIF: +3.2, DEA: +1.8)\nMA20 = 952 (價格在上方)\nKD: K=62, D=58\nBollinger: 上軌 998 / 中軌 952 / 下軌 906`,
        cost_ms: 156,
      },
      {
        phase: 'PROMPT',
        t: '08:30:02',
        label: '組裝 Prompt (system + user)',
        body: `Tokens: 12,480 in\n包含：技術指標、籌碼、新聞、風控規則`,
        cost_ms: 88,
      },
      {
        phase: 'LLM',
        t: '08:30:02',
        label: 'Claude Sonnet 4.6 推理',
        body: `模型：claude-sonnet-4-6\nAPI 請求：POST /v1/messages\n串流模式：SSE\n耗時：14,820 ms`,
        cost_ms: 14820,
        cost_usd: 0.0846,
      },
      {
        phase: 'PARSE',
        t: '08:30:17',
        label: '解析 JSON 輸出',
        body: `signal: "buy"\nconfidence: 0.86\ntarget_price: 1020\nstop_loss_price: 940`,
        cost_ms: 34,
      },
      {
        phase: 'GUARD',
        t: '08:30:17',
        label: '風控規則驗證',
        body: `✓ 信心門檻 0.86 ≥ 0.75\n✓ 單筆部位 NT$142,000 ≤ 上限 25%\n! 板塊集中度 38% 接近 40% 上限\n✓ 黑名單無衝突`,
        cost_ms: 92,
      },
      {
        phase: 'OUTPUT',
        t: '08:30:17',
        label: '輸出最終判決',
        body: `BUY 台積電 (${code}) | 信心 86% | TP 1,020 | SL 940\nTelegram 已推送：08:30:18`,
        cost_ms: 78,
      },
    ],
    prompt: {
      system: `你是一個台股量化交易 AI 分析師，擅長結合技術分析、基本面與籌碼面做出交易判決。
你的任務是分析指定股票並給出：
1. 交易訊號 (buy/hold/sell)
2. 信心分數 (0.0~1.0)
3. 目標價與停損價
4. 簡短進場理由 (繁體中文)

規則：
- 僅在信心 ≥ 0.75 時給出 buy
- 目標價 / 停損價需基於技術面支撐阻力
- 理由必須包含具體數據`,
      user: `請分析以下股票：

股票代碼：${code}
日期：2026-05-25

<external_data>
技術指標：
  RSI(14) = 62.4（回測支撐區）
  MACD = 金叉（DIF +3.2 > DEA +1.8）
  MA20 = 952（價格在上方 +2.4%）
  KD = K:62 D:58（K在D上方）
  布林通道：上軌 998 / 下軌 906
  成交量：38,421張（+28% vs 5日均量）

籌碼面：
  外資：連買 5 日，淨買超 12,840 張
  投信：買超 3,200 張
  大股東：持股比例 77.3%（上季 76.8%）

新聞摘要：
  1. CoWoS 封裝擴產計畫提前，H200/B200 需求持續
  2. N2 良率超過原定目標，量產時程提前
  3. 法說會暗示 Q3 毛利率有望回升至 55%+
</external_data>

請給出你的分析與判決。`,
      tokens_in: 12480,
    },
    response: {
      raw: `{
  "signal": "buy",
  "confidence": 0.86,
  "target_price": 1020,
  "stop_loss_price": 940,
  "reason": "法人連買5日，突破前高壓力，RSI回測支撐，N2良率超預期與CoWoS擴產為強烈催化劑"
}`,
      parsed: {
        signal: 'buy',
        confidence: 0.86,
        target_price: 1020,
        stop_loss_price: 940,
        reason: '法人連買5日，突破前高壓力，RSI回測支撐，N2良率超預期與CoWoS擴產為強烈催化劑',
      },
      tokens_out: 3840,
    },
    contributions: [
      { key: '法人籌碼', detail: '外資+投信連買5日', delta: 0.18, kind: 'positive' },
      { key: 'MACD金叉', detail: 'DIF穿越DEA向上', delta: 0.14, kind: 'positive' },
      { key: 'RSI支撐', detail: '62.4 回測50支撐未破', delta: 0.12, kind: 'positive' },
      { key: 'CoWoS消息', detail: '擴產計畫提前', delta: 0.11, kind: 'positive' },
      { key: 'MA20多頭', detail: '價格站上均線+2.4%', delta: 0.09, kind: 'positive' },
      { key: '量增價漲', detail: '+28% vs 5日均量', delta: 0.08, kind: 'positive' },
      { key: '板塊集中', detail: '半導體38%接近上限', delta: -0.04, kind: 'negative' },
      { key: 'KD中性', detail: '尚未超買但動能仍在', delta: 0.00, kind: 'base' },
    ],
    final_confidence: 0.86,
    self_check: [
      { question: '信心分數是否有足夠的多方證據支持？', answer: '是。法人、技術、基本面三面共振，六項正面指標。', passed: true },
      { question: '停損設定是否合理？', answer: '940 為近期低點支撐，跌破代表趨勢反轉，合理。', passed: true },
      { question: '目標價是否基於技術阻力？', answer: '1020 為前波高點，上方有明確阻力位，合理。', passed: true },
      { question: '是否有重大風險未納入考量？', answer: '板塊集中度38%接近上限，地緣政治風險未充分量化，應適度謹慎。', passed: false },
    ],
    decision_hash: 'sha256:8f3d2a1b9c4e7f6d0e5a3b8c2d1f4e9a7b6c3d2e1f0a8b7c6d5e4f3a2b1c0d',
  };
}

// ── Cursor blink ───────────────────────────────────────────────────────────────
function BlinkingCursor() {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const id = setInterval(() => setVisible((v) => !v), 530);
    return () => clearInterval(id);
  }, []);
  return (
    <span style={{
      display: 'inline-block', width: 7, height: 13,
      background: visible ? 'var(--up)' : 'transparent',
      verticalAlign: 'middle', marginLeft: 2,
    }} />
  );
}

// ── Step modal ─────────────────────────────────────────────────────────────────
function StepModal({ step, onClose }: { step: { phase: string; label: string; body: string }; onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(20,23,31,0.7)',
        zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--surface)', border: '1px solid var(--hair)',
          width: 560, maxHeight: '70vh', overflow: 'auto',
        }}
      >
        <div style={{
          padding: '10px 16px', borderBottom: '1px solid var(--hair)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>{step.phase} · {step.label}</span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: 'var(--muted)' }}>✕</button>
        </div>
        <pre style={{
          padding: 16, margin: 0, fontFamily: 'var(--font-mono)',
          fontFeatureSettings: '"tnum" 1, "zero" 1', fontSize: 11,
          color: 'var(--ink-2)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          lineHeight: 1.6,
        }}>
          {step.body}
        </pre>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function Reasoning() {
  const { code } = useParams<{ code: string }>();
  const [trace, setTrace] = useState<ReasoningTrace | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedStep, setSelectedStep] = useState<ReasoningTrace['steps'][0] | null>(null);
  const [copied, setCopied] = useState(false);
  const hashRef = useRef<HTMLSpanElement>(null);
  void hashRef; // used as ref attachment below

  useEffect(() => {
    if (!code) return;
    api.getReasoning(code)
      .then(setTrace)
      .catch(() => setTrace(mockReasoningTrace(code)))
      .finally(() => setLoading(false));
  }, [code]);

  function handleCopyHash() {
    if (!trace) return;
    navigator.clipboard.writeText(trace.decision_hash).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  if (loading) {
    return (
      <AppChrome title={`推理過程 · ${code}`} eyebrow="03.3">
        <div style={{ padding: 20, color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>載入中…</div>
      </AppChrome>
    );
  }

  const t = trace ?? mockReasoningTrace(code ?? '2330');
  const totalCostMs = t.steps.reduce((s, step) => s + (step.cost_ms ?? 0), 0);
  const llmCostUsd = t.steps.find((s) => s.phase === 'LLM')?.cost_usd ?? 0;

  return (
    <AppChrome title={`${t.code} · 推理過程`} eyebrow="03.3">
      {selectedStep && (
        <StepModal step={selectedStep} onClose={() => setSelectedStep(null)} />
      )}

      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1.2fr 1fr',
        gap: 0, height: '100%', overflow: 'hidden',
      }}>

        {/* ── LEFT: Trace timeline ─────────────────────────────── */}
        <div style={{
          borderRight: '1px solid var(--hair)',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid var(--hair)',
            background: 'var(--surface)', flexShrink: 0,
            fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
            fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.14em',
          }}>
            推理流水線 · {t.steps.length} 步
          </div>

          <div style={{ flex: 1, overflow: 'auto' }}>
            {t.steps.map((step, i) => (
              <TraceStep
                key={i}
                step={step}
                index={i}
                isLLM={step.phase === 'LLM'}
                onClick={() => setSelectedStep(step)}
              />
            ))}
          </div>

          {/* Cost breakdown card */}
          <div style={{ flexShrink: 0, borderTop: '1px solid var(--hair)' }}>
            <Card label="成本明細" padding={12}>
              <div style={{
                display: 'grid', gridTemplateColumns: '1fr 1fr',
                gap: '6px 12px',
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 11,
              }}>
                {[
                  { label: 'Tokens In', value: t.prompt.tokens_in.toLocaleString() },
                  { label: 'Tokens Out', value: t.response.tokens_out.toLocaleString() },
                  { label: '總耗時', value: (totalCostMs / 1000).toFixed(2) + 's' },
                  { label: '費用 (USD)', value: '$' + llmCostUsd.toFixed(4), color: 'var(--gold)' },
                ].map((item) => (
                  <div key={item.label}>
                    <div style={{ fontSize: 10, color: 'var(--muted)' }}>{item.label}</div>
                    <div style={{ fontWeight: 600, color: (item as { color?: string }).color ?? 'var(--ink)' }}>{item.value}</div>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        </div>

        {/* ── MIDDLE: Prompt + Response ────────────────────────── */}
        <div style={{
          background: 'var(--ink-bg)',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          borderRight: '1px solid var(--dark-hair)',
        }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid var(--dark-hair)', flexShrink: 0,
            fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
            fontSize: 10, color: 'var(--dark-muted)', textTransform: 'uppercase', letterSpacing: '0.14em',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span>Prompt / Response</span>
            <span style={{ color: 'var(--dark-hair)' }}>·</span>
            <span style={{ color: 'var(--dark-muted)' }}>
              {(t.prompt.tokens_in + t.response.tokens_out).toLocaleString()} tokens
            </span>
          </div>

          <div style={{ flex: 1, overflow: 'auto', padding: '12px 0' }}>

            {/* SYSTEM block */}
            <div style={{ marginBottom: 12 }}>
              <div style={{
                padding: '6px 14px',
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 10, color: 'var(--dark-muted)', letterSpacing: '0.12em',
                textTransform: 'uppercase',
              }}>
                SYSTEM
              </div>
              <div style={{
                margin: '0 12px',
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid var(--dark-hair)',
                padding: '10px 12px',
              }}>
                <pre style={{
                  margin: 0, fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                  fontSize: 11, color: 'var(--dark-ink)', whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word', lineHeight: 1.6,
                }}>
                  {t.prompt.system}
                </pre>
              </div>
            </div>

            {/* USER block */}
            <div style={{ marginBottom: 12 }}>
              <div style={{
                padding: '6px 14px',
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 10, color: 'var(--dark-muted)', letterSpacing: '0.12em',
                textTransform: 'uppercase',
              }}>
                USER · INDICATORS
              </div>
              <div style={{
                margin: '0 12px',
                border: '1px solid var(--dark-hair)',
                padding: '10px 12px',
              }}>
                <pre style={{
                  margin: 0, fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                  fontSize: 11, color: 'var(--dark-ink)', whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word', lineHeight: 1.6,
                }}>
                  {t.prompt.user}
                </pre>
              </div>
            </div>

            {/* ASSISTANT block */}
            <div>
              <div style={{
                padding: '6px 14px',
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 10, letterSpacing: '0.12em',
                textTransform: 'uppercase',
                color: 'var(--up)',
              }}>
                ASSISTANT · STREAMING
              </div>
              <div style={{
                margin: '0 12px',
                borderLeft: '3px solid var(--up)',
                paddingLeft: 10,
                border: '1px solid var(--dark-hair)',
                borderLeftWidth: 3,
                borderLeftColor: 'var(--up)',
                padding: '10px 10px 10px 12px',
              }}>
                <pre style={{
                  margin: 0, fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                  fontSize: 11, color: 'var(--dark-ink)', whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word', lineHeight: 1.6,
                }}>
                  {t.response.raw}
                  <BlinkingCursor />
                </pre>
              </div>
            </div>

          </div>
        </div>

        {/* ── RIGHT: Decision breakdown ─────────────────────────── */}
        <div style={{
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid var(--hair)',
            background: 'var(--surface)', flexShrink: 0,
            fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
            fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.14em',
          }}>
            判決構成
          </div>

          <div style={{ flex: 1, overflow: 'auto' }}>

            {/* Contributions */}
            <div style={{ borderBottom: '1px solid var(--hair)', marginBottom: 0 }}>
              <div style={{
                padding: '6px 12px', background: 'var(--surface-2)',
                fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em',
              }}>
                信心影響因子
              </div>
              {t.contributions.map((c) => (
                <ContribRow key={c.key} contrib={c} />
              ))}
              {/* Final confidence total */}
              <div style={{
                padding: '8px 12px', background: 'var(--surface-2)',
                borderTop: '2px solid var(--hair)', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <span style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>最終信心</span>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                  fontSize: 18, fontWeight: 600,
                  color: t.final_confidence >= 0.75 ? 'var(--up)' : t.final_confidence >= 0.60 ? 'var(--gold)' : 'var(--muted)',
                }}>
                  {(t.final_confidence * 100).toFixed(0)}%
                </span>
              </div>
            </div>

            {/* Self-check Q/A */}
            <div style={{ borderBottom: '1px solid var(--hair)' }}>
              <div style={{
                padding: '6px 12px', background: 'var(--surface-2)',
                fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.1em',
              }}>
                模型自檢
              </div>
              {t.self_check.map((sc, i) => (
                <div key={i} style={{
                  padding: '10px 12px', borderBottom: i < t.self_check.length - 1 ? '1px solid var(--hair)' : 'none',
                }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 4 }}>
                    <span style={{
                      flexShrink: 0, fontSize: 12, fontWeight: 600,
                      color: sc.passed ? 'var(--down)' : 'var(--up)',
                    }}>
                      {sc.passed ? '✓' : '✗'}
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--ink)', fontWeight: 500, lineHeight: 1.4 }}>
                      {sc.question}
                    </span>
                  </div>
                  <div style={{ paddingLeft: 20, fontSize: 11, color: 'var(--muted)', lineHeight: 1.5 }}>
                    {sc.answer}
                  </div>
                </div>
              ))}
            </div>

            {/* Decision TRACE card */}
            <div style={{
              background: 'var(--ink-bg)', borderTop: '1px solid var(--dark-hair)',
              padding: 14,
            }}>
              <div style={{
                fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                fontSize: 10, color: 'var(--dark-muted)',
                letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 10,
              }}>
                DECISION TRACE
              </div>
              {[
                { label: 'RUN-ID', value: t.run_id },
                { label: 'CODE', value: t.code },
                { label: 'SIGNAL', value: t.response.parsed.signal.toUpperCase() },
                { label: 'CONFIDENCE', value: (t.final_confidence * 100).toFixed(0) + '%' },
                { label: 'DURATION', value: (t.total_duration_ms / 1000).toFixed(2) + 's' },
                { label: 'MODEL', value: 'claude-sonnet-4-6' },
              ].map((item) => (
                <div key={item.label} style={{
                  display: 'flex', justifyContent: 'space-between', marginBottom: 5,
                  fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                  fontSize: 10,
                }}>
                  <span style={{ color: 'var(--dark-muted)' }}>{item.label}</span>
                  <span style={{ color: 'var(--dark-ink)', fontWeight: 500 }}>{item.value}</span>
                </div>
              ))}
              {/* Hash */}
              <div style={{
                marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--dark-hair)',
              }}>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                  fontSize: 9, color: 'var(--dark-muted)', letterSpacing: '0.1em',
                  marginBottom: 4, textTransform: 'uppercase',
                }}>
                  Decision Hash
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span
                    ref={hashRef}
                    style={{
                      fontFamily: 'var(--font-mono)', fontFeatureSettings: '"tnum" 1, "zero" 1',
                      fontSize: 9, color: 'var(--dark-ink)',
                      flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}
                  >
                    {t.decision_hash}
                  </span>
                  <button
                    onClick={handleCopyHash}
                    style={{
                      flexShrink: 0, background: 'transparent',
                      border: '1px solid var(--dark-hair)',
                      color: copied ? 'var(--down)' : 'var(--dark-muted)',
                      fontFamily: 'var(--font-mono)', fontSize: 9,
                      padding: '3px 8px', cursor: 'pointer', borderRadius: 2,
                    }}
                  >
                    {copied ? '已複製' : '複製'}
                  </button>
                </div>
              </div>
            </div>

          </div>
        </div>

      </div>
    </AppChrome>
  );
}
