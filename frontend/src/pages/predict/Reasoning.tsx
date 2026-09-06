// 03.3 AI 預測 · 推理過程 — Wave 3-G
import { useEffect, useState, useRef } from 'react';
import { useParams } from 'react-router-dom';
import AppChrome from '../../components/AppChrome';
import TraceStep from '../../components/TraceStep';
import ContribRow from '../../components/ContribRow';
import Card from '../../components/Card';
import { api } from '../../api';
import { queryState } from '../../components/DataState';
import type { ReasoningTrace } from '../../types';

// ── Mock data ──────────────────────────────────────────────────────────────────
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
  const [loadError, setLoadError] = useState<unknown>(null);
  const [selectedStep, setSelectedStep] = useState<ReasoningTrace['steps'][0] | null>(null);
  const [copied, setCopied] = useState(false);
  const hashRef = useRef<HTMLSpanElement>(null);
  void hashRef; // used as ref attachment below

  useEffect(() => {
    if (!code) return;
    api.getReasoning(code)
      .then(setTrace)
      .catch(setLoadError)
      .finally(() => setLoading(false));
  }, [code]);

  function handleCopyHash() {
    if (!trace) return;
    navigator.clipboard.writeText(trace.decision_hash).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  const state = queryState({
    isLoading: loading, isError: !!loadError, error: loadError, isEmpty: !trace,
    what: `${code ?? ''} 的推理過程`,
    emptyDetail: '只有經過 AI 深度分析的候選才會留下推理紀錄（每日前 8 名）。',
  });
  if (state) return <AppChrome title={`推理過程 · ${code}`} eyebrow="03.3">{state}</AppChrome>;

  const t = trace!;
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
