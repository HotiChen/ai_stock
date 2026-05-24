import React from 'react';
import Sparkline from './Sparkline';

interface KpiProps {
  label: string;
  value: React.ReactNode;  // 可以是數字或字串
  sub?: string;
  spark?: number[];
  valueColor?: string;
}

export default function Kpi({ label, value, sub, spark, valueColor }: KpiProps) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      padding: '10px 14px',
      background: 'var(--surface)',
      border: '1px solid var(--hair)',
      minWidth: 0,
    }}>
      {/* Label */}
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 10,
        letterSpacing: '0.12em',
        textTransform: 'uppercase',
        color: 'var(--muted)',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}>
        {label}
      </div>

      {/* Value row */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, justifyContent: 'space-between' }}>
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontFeatureSettings: '"tnum" 1, "zero" 1',
          fontSize: 22,
          fontWeight: 500,
          lineHeight: 1.25,
          color: valueColor ?? 'var(--ink)',
          whiteSpace: 'nowrap',
        }}>
          {value}
        </div>
        {spark && spark.length >= 2 && (
          <Sparkline data={spark} width={60} height={22} filled />
        )}
      </div>

      {/* Sub */}
      {sub && (
        <div style={{
          fontSize: 11,
          color: 'var(--muted)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {sub}
        </div>
      )}
    </div>
  );
}
