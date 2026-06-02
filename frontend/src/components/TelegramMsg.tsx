interface TelegramMsgProps {
  role: 'bot' | 'user';
  content: string;
  time?: string;
  isWarning?: boolean;
  buttons?: { label: string; onClick?: () => void }[];
}

export default function TelegramMsg({
  role,
  content,
  time,
  isWarning = false,
  buttons,
}: TelegramMsgProps) {
  const isBot = role === 'bot';

  return (
    <div style={{
      display: 'flex',
      justifyContent: isBot ? 'flex-start' : 'flex-end',
      padding: '4px 12px',
    }}>
      <div style={{
        maxWidth: '80%',
        background: isBot
          ? (isWarning ? 'var(--gold-soft)' : 'var(--surface)')
          : 'var(--surface-2)',
        border: `1px solid ${isWarning ? 'var(--gold)' : 'var(--hair)'}`,
        // Telegram bubble: border-radius 4–8px (exception rule)
        borderRadius: isBot ? '4px 8px 8px 4px' : '8px 4px 4px 8px',
        padding: '8px 12px',
        position: 'relative',
      }}>
        {/* Content */}
        <div style={{
          fontSize: 12,
          color: isWarning ? 'var(--gold)' : 'var(--ink-2)',
          lineHeight: 1.55,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}>
          {content}
        </div>

        {/* Inline buttons */}
        {buttons && buttons.length > 0 && (
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 6,
            marginTop: 8,
          }}>
            {buttons.map((btn, i) => (
              <button
                key={i}
                onClick={btn.onClick}
                style={{
                  padding: '4px 12px',
                  fontSize: 11,
                  fontWeight: 500,
                  background: 'var(--surface)',
                  border: '1px solid var(--hair-2)',
                  borderRadius: 4,
                  color: 'var(--ink)',
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                {btn.label}
              </button>
            ))}
          </div>
        )}

        {/* Time */}
        {time && (
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontFeatureSettings: '"tnum" 1, "zero" 1',
            fontSize: 10,
            color: 'var(--muted-2)',
            marginTop: 4,
            textAlign: isBot ? 'left' : 'right',
          }}>
            {time}
          </div>
        )}
      </div>
    </div>
  );
}
