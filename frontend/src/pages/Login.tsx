// 01 登入頁 — Wave 2-E 完整實作
// Placeholder for routing

export default function Login() {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100vh',
      background: 'var(--bg)',
    }}>
      <div style={{
        width: 320,
        background: 'var(--surface)',
        border: '1px solid var(--hair)',
        padding: 32,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}>
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontWeight: 600,
          fontSize: 16,
          letterSpacing: '0.06em',
          color: 'var(--ink)',
          textAlign: 'center',
        }}>
          QUANT·AI
        </div>
        <div style={{
          fontSize: 11,
          color: 'var(--muted)',
          textAlign: 'center',
        }}>
          台股 AI 量化交易工作站
        </div>
        <div style={{
          marginTop: 8,
          padding: 16,
          background: 'var(--gold-soft)',
          border: '1px solid var(--gold)',
          fontSize: 11,
          color: 'var(--gold)',
        }}>
          TODO: Wave 2-E 實作登入表單
        </div>
      </div>
    </div>
  );
}
