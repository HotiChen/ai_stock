import { useEffect, useRef } from 'react';

interface CountdownBarProps {
  seconds: number;         // 距 13:25 的秒數
  monitoringCount: number;
  closedCount: number;
  slTriggered: number;
}

// Trading window: 09:00–13:25 = 4h25m = 265min = 15900s
const TOTAL_SECONDS = 15_900;

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return '00:00';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export default function CountdownBar({
  seconds,
  monitoringCount,
  closedCount,
  slTriggered,
}: CountdownBarProps) {
  const numRef = useRef<HTMLSpanElement>(null);

  // Flash animation when < 60s
  useEffect(() => {
    if (!numRef.current) return;
    if (seconds > 0 && seconds < 60) {
      numRef.current.style.animation = 'countdown-blink 1s step-start infinite';
    } else {
      numRef.current.style.animation = 'none';
    }
  }, [seconds < 60]);

  const elapsed = TOTAL_SECONDS - seconds;
  const progress = Math.max(0, Math.min(1, elapsed / TOTAL_SECONDS));
  const isUrgent = seconds < 60;
  const isClosed = seconds <= 0;

  return (
    <div style={{
      background: 'var(--ink-bg)',
      color: 'var(--dark-ink)',
      padding: '10px 14px',
      fontFamily: 'var(--font-mono)',
      fontFeatureSettings: '"tnum" 1, "zero" 1',
    }}>
      <style>{`
        @keyframes countdown-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
      `}</style>

      {/* Top row: big countdown + stats */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, marginBottom: 8 }}>
        <span
          ref={numRef}
          style={{
            fontSize: 28,
            fontWeight: 500,
            color: isUrgent ? 'var(--up)' : 'var(--dark-ink)',
            lineHeight: 1.2,
          }}
        >
          {isClosed ? '收盤' : formatCountdown(seconds)}
        </span>

        <span style={{ fontSize: 10, color: 'var(--dark-muted)' }}>
          距 13:25 強制平倉
        </span>

        <div style={{ flex: 1 }} />

        {/* Stats */}
        <div style={{ display: 'flex', gap: 14, fontSize: 11 }}>
          <span>
            <span style={{ color: 'var(--dark-muted)' }}>監控 </span>
            <span style={{ color: 'var(--dark-ink)' }}>{monitoringCount}</span>
          </span>
          <span>
            <span style={{ color: 'var(--dark-muted)' }}>已平 </span>
            <span style={{ color: 'var(--down)' }}>{closedCount}</span>
          </span>
          <span>
            <span style={{ color: 'var(--dark-muted)' }}>停損 </span>
            <span style={{ color: slTriggered > 0 ? 'var(--up)' : 'var(--dark-muted)' }}>
              {slTriggered}
            </span>
          </span>
        </div>
      </div>

      {/* Progress bar */}
      <div style={{
        height: 3,
        background: 'var(--dark-hair)',
        borderRadius: 1,
        overflow: 'hidden',
      }}>
        <div style={{
          height: '100%',
          width: `${progress * 100}%`,
          background: isUrgent ? 'var(--up)' : 'var(--dark-muted)',
          transition: 'width 1s linear',
        }} />
      </div>

      {/* Time labels */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        marginTop: 3,
        fontSize: 10,
        color: 'var(--dark-muted)',
      }}>
        <span>09:00</span>
        <span>13:25</span>
      </div>
    </div>
  );
}
