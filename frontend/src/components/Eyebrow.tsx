import React from 'react';

interface EyebrowProps {
  label: string;
  right?: React.ReactNode;
}

export default function Eyebrow({ label, right }: EyebrowProps) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '10px',
      font: '500 10px/1.4 var(--font-mono)',
      color: 'var(--muted)',
      letterSpacing: '0.18em',
      textTransform: 'uppercase',
    }}>
      <span style={{ whiteSpace: 'nowrap' }}>{label}</span>
      <span style={{
        flex: 1,
        height: '1px',
        background: 'var(--hair)',
        display: 'block',
      }} />
      {right && <span style={{ whiteSpace: 'nowrap' }}>{right}</span>}
    </div>
  );
}
