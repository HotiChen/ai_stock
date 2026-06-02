import React from 'react';

const shimmer: React.CSSProperties = {
  background: 'linear-gradient(90deg, var(--hair) 25%, var(--surface-2) 50%, var(--hair) 75%)',
  backgroundSize: '200% 100%',
  animation: 'skeleton-shimmer 1.4s ease infinite',
  borderRadius: '2px',
};

// Inject animation once
const styleId = 'page-skeleton-style';
if (typeof document !== 'undefined' && !document.getElementById(styleId)) {
  const style = document.createElement('style');
  style.id = styleId;
  style.textContent = `
    @keyframes skeleton-shimmer {
      0%   { background-position: 200% 0; }
      100% { background-position: -200% 0; }
    }
  `;
  document.head.appendChild(style);
}

interface BlockProps {
  width?: string | number;
  height?: number;
  style?: React.CSSProperties;
}

function SkeletonBlock({ width = '100%', height = 14, style }: BlockProps) {
  return (
    <div style={{ width, height, ...shimmer, ...style }} />
  );
}

export default function PageSkeleton() {
  return (
    <div style={{
      display: 'flex',
      height: '100vh',
      background: 'var(--bg)',
      flexDirection: 'column',
    }}>
      {/* TopBar skeleton */}
      <div style={{
        height: 44,
        borderBottom: '1px solid var(--hair)',
        background: 'var(--surface)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px',
        gap: 12,
      }}>
        <SkeletonBlock width={120} height={18} />
        <div style={{ flex: 1 }} />
        <SkeletonBlock width={80} height={14} />
        <SkeletonBlock width={60} height={14} />
      </div>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Sidebar skeleton */}
        <div style={{
          width: 200,
          borderRight: '1px solid var(--hair)',
          background: 'var(--surface)',
          padding: '12px 0',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
        }}>
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} style={{ padding: '0 14px', height: 38, display: 'flex', alignItems: 'center' }}>
              <SkeletonBlock width={`${60 + (i % 3) * 20}%`} height={12} />
            </div>
          ))}
        </div>

        {/* Content skeleton */}
        <div style={{ flex: 1, padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <SkeletonBlock width="30%" height={20} />
          <div style={{ display: 'flex', gap: 12 }}>
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} style={{
                flex: 1,
                background: 'var(--surface)',
                border: '1px solid var(--hair)',
                padding: 12,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}>
                <SkeletonBlock width="50%" height={10} />
                <SkeletonBlock width="70%" height={20} />
              </div>
            ))}
          </div>
          <div style={{
            flex: 1,
            background: 'var(--surface)',
            border: '1px solid var(--hair)',
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}>
            {Array.from({ length: 6 }).map((_, i) => (
              <SkeletonBlock key={i} width={`${50 + Math.random() * 40}%`} height={12} />
            ))}
          </div>
        </div>
      </div>

      {/* StatusBar skeleton */}
      <div style={{
        height: 22,
        borderTop: '1px solid var(--hair)',
        background: 'var(--surface)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 12px',
        gap: 16,
      }}>
        {Array.from({ length: 5 }).map((_, i) => (
          <SkeletonBlock key={i} width={60} height={10} />
        ))}
      </div>
    </div>
  );
}
