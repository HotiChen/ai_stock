import React from 'react';

interface PillProps {
  children: React.ReactNode;
  color?: string;      // text color
  bg?: string;         // background
  border?: string;     // border color
  size?: 10 | 11 | 12;
}

export default function Pill({
  children,
  color,
  bg,
  border,
  size = 11,
}: PillProps) {
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      padding: '2px 7px',
      borderRadius: '2px',
      fontFamily: 'var(--font-mono)',
      fontFeatureSettings: '"tnum" 1, "zero" 1',
      fontSize: `${size}px`,
      letterSpacing: '0.04em',
      lineHeight: 1.4,
      color: color ?? 'var(--ink)',
      background: bg ?? 'transparent',
      border: border ? `1px solid ${border}` : '1px solid var(--hair)',
      whiteSpace: 'nowrap',
    }}>
      {children}
    </span>
  );
}
