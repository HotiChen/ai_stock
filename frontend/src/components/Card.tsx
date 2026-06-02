import React from 'react';
import Eyebrow from './Eyebrow';

interface CardProps {
  label?: string;           // eyebrow 左側標籤
  right?: React.ReactNode;  // eyebrow 右側
  padding?: number | string;
  children: React.ReactNode;
  style?: React.CSSProperties;
}

export default function Card({
  label,
  right,
  padding = 16,
  children,
  style,
}: CardProps) {
  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--hair)',
      borderRadius: 0,
      ...style,
    }}>
      {label && (
        <div style={{
          padding: '10px 14px',
          borderBottom: '1px solid var(--hair)',
        }}>
          <Eyebrow label={label} right={right} />
        </div>
      )}
      <div style={{
        padding: typeof padding === 'number' ? `${padding}px` : padding,
      }}>
        {children}
      </div>
    </div>
  );
}
