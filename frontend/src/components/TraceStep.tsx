import type { TraceStep as TraceStepType } from '../types';
import Pill from './Pill';

interface TraceStepProps {
  step: TraceStepType;
  index: number;
  isLLM?: boolean;   // LLM 步驟紅色強調
  onClick?: () => void;
}

const phaseColor: Record<string, string> = {
  INPUT:  'var(--muted)',
  FETCH:  'var(--muted)',
  EVAL:   'var(--gold)',
  PROMPT: 'var(--gold)',
  LLM:    'var(--up)',
  PARSE:  'var(--muted)',
  GUARD:  'var(--down)',
  OUTPUT: 'var(--ink)',
};

const phaseBg: Record<string, string> = {
  INPUT:  'transparent',
  FETCH:  'transparent',
  EVAL:   'var(--gold-soft)',
  PROMPT: 'var(--gold-soft)',
  LLM:    'var(--up-soft)',
  PARSE:  'transparent',
  GUARD:  'var(--down-soft)',
  OUTPUT: 'var(--surface-2)',
};

export default function TraceStep({ step, index, isLLM = false, onClick }: TraceStepProps) {
  const color = isLLM ? 'var(--up)' : (phaseColor[step.phase] ?? 'var(--muted)');
  const bg = isLLM ? 'var(--up-soft)' : (phaseBg[step.phase] ?? 'transparent');

  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex',
        gap: 10,
        padding: '8px 12px',
        borderBottom: '1px solid var(--hair)',
        borderLeft: isLLM ? '3px solid var(--up)' : '3px solid transparent',
        cursor: onClick ? 'pointer' : 'default',
        background: 'var(--surface)',
      }}
    >
      {/* Step index */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1, "zero" 1',
        fontSize: 10,
        color: 'var(--muted-2)',
        width: 20,
        flexShrink: 0,
        paddingTop: 2,
      }}>
        {String(index + 1).padStart(2, '0')}
      </span>

      {/* Phase pill */}
      <Pill color={color} bg={bg} border={color} size={10}>
        {step.phase}
      </Pill>

      {/* Time */}
      <span style={{
        fontFamily: 'var(--font-mono)',
        fontFeatureSettings: '"tnum" 1, "zero" 1',
        fontSize: 10,
        color: 'var(--muted)',
        flexShrink: 0,
        width: 56,
        paddingTop: 2,
      }}>
        {step.t}
      </span>

      {/* Label + body */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontWeight: isLLM ? 600 : 400,
          fontSize: 12,
          color: isLLM ? 'var(--up)' : 'var(--ink)',
          marginBottom: step.body ? 2 : 0,
        }}>
          {step.label}
        </div>
        {step.body && (
          <div style={{
            fontSize: 11,
            color: 'var(--muted)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}>
            {step.body}
          </div>
        )}
      </div>

      {/* Cost */}
      {step.cost_ms != null && (
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontFeatureSettings: '"tnum" 1, "zero" 1',
          fontSize: 10,
          color: 'var(--muted)',
          flexShrink: 0,
          textAlign: 'right',
        }}>
          <div>{step.cost_ms}ms</div>
          {step.cost_usd != null && (
            <div style={{ color: 'var(--muted-2)' }}>${step.cost_usd.toFixed(4)}</div>
          )}
        </div>
      )}
    </div>
  );
}
