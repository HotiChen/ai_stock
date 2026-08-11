// 05.2 學習日誌 + AI 顧問
import { useState, useRef, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import AppChrome from '../components/AppChrome';
import EmptyHint from '../components/EmptyHint';
import { api } from '../api';
import type { JournalEntry, ChatMessage } from '../types';

// 本頁一律使用真實 API 資料。先前這裡有 MOCK_ENTRIES／MOCK_CHAT，會在沒有資料時
// 靜默頂替，導致畫面出現看似真實的假交易日誌與假 AI 對話——已全部移除。
// 沒有資料時請顯示空狀態（見 EmptyHint），不要編造。

function JournalCard({ entry }: { entry: JournalEntry }) {
  const isProfit = entry.pnl >= 0;
  const borderColor = isProfit ? 'var(--up)' : 'var(--down)';
  const pnlColor = isProfit ? 'var(--up)' : 'var(--down)';

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 4, padding: 16, display: 'flex', gap: 12 }}>
      <div style={{ width: 3, background: borderColor, borderRadius: 2, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
          <div>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--muted)' }}>{entry.date}</span>
            <span style={{ margin: '0 8px', color: 'var(--border)' }}>·</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600 }}>{entry.code}</span>
            <span style={{ marginLeft: 6, fontSize: 13, color: 'var(--fg)' }}>{entry.name}</span>
          </div>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 700, color: pnlColor }}>
            {isProfit ? '+' : ''}{entry.pnl.toLocaleString()}
          </span>
        </div>
        <p style={{ fontSize: 12, color: 'var(--fg)', lineHeight: 1.6, margin: '0 0 10px' }}>{entry.lesson}</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {entry.tags.map(tag => (
            <span key={tag} style={{ fontSize: 10, padding: '2px 7px', borderRadius: 2, background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>{tag}</span>
          ))}
          {entry.rule_updated && (
            <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 2, background: 'var(--accent)', color: '#fff', fontFamily: 'var(--font-mono)' }}>✓ 已寫入 rules.py</span>
          )}
          <button onClick={() => {}} style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
            檢視交易詳情 →
          </button>
        </div>
      </div>
    </div>
  );
}

function ChatBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user';
  return (
    <div style={{ display: 'flex', flexDirection: isUser ? 'row-reverse' : 'row', gap: 8, alignItems: 'flex-start' }}>
      <div style={{ width: 28, height: 28, borderRadius: '50%', background: isUser ? 'var(--accent)' : '#333', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: '#fff', flexShrink: 0, fontFamily: 'var(--font-mono)' }}>
        {isUser ? 'U' : 'AI'}
      </div>
      <div style={{ maxWidth: '85%', background: isUser ? 'var(--accent)' : 'var(--surface)', border: isUser ? 'none' : '1px solid var(--border)', borderRadius: 4, padding: '8px 12px' }}>
        {!isUser && msg.model && (
          <div style={{ fontSize: 9, color: 'var(--muted)', marginBottom: 4, fontFamily: 'var(--font-mono)', letterSpacing: 0.5 }}>SONNET · {msg.model}</div>
        )}
        <div style={{ fontSize: 12, lineHeight: 1.6, color: isUser ? '#fff' : 'var(--fg)', whiteSpace: 'pre-wrap' }}>
          {msg.content.split('\n').map((line, i) => {
            const boldLine = line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            return <p key={i} style={{ margin: i === 0 ? 0 : '4px 0 0' }} dangerouslySetInnerHTML={{ __html: boldLine }} />;
          })}
        </div>
        <div style={{ fontSize: 9, color: isUser ? 'rgba(255,255,255,0.6)' : 'var(--muted)', marginTop: 4, fontFamily: 'var(--font-mono)', textAlign: isUser ? 'left' : 'right' }}>
          {new Date(msg.ts).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })}
        </div>
      </div>
    </div>
  );
}

export default function Journal() {
  const { data: entries = [] } = useQuery({
    queryKey: ['journal'],
    queryFn: () => api.getJournal(),
    retry: false,
  });

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getJournalChatHistory().then(setMessages).catch(() => setMessages([]));
  }, []);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 以真實 entries 推導 KPI；沒有真實資料就不編造 147 筆交易／63.3% 勝率這類數字。
  const totalTrades = entries.length;
  const winRate = entries.length ? (entries.filter(e => e.pnl >= 0).length / entries.length) * 100 : null;
  const rulesUpdatedCount = entries.filter(e => e.rule_updated).length;
  const totalPnl = entries.reduce((sum, e) => sum + e.pnl, 0);

  async function sendMessage() {
    if (!input.trim() || streaming) return;
    const userMsg: ChatMessage = { id: Date.now().toString(), role: 'user', content: input, ts: new Date().toISOString() };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setStreaming(true);

    const aiMsgId = (Date.now() + 1).toString();
    const aiMsg: ChatMessage = { id: aiMsgId, role: 'assistant', content: '', ts: new Date().toISOString(), model: 'claude-sonnet-4-6' };
    setMessages(prev => [...prev, aiMsg]);

    try {
      const resp = await api.sendChat([...messages, userMsg]);
      const reader = resp.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) return;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') break;
            try {
              const parsed = JSON.parse(data);
              const delta = parsed.choices?.[0]?.delta?.content ?? '';
              if (delta) {
                setMessages(prev => prev.map(m => m.id === aiMsgId ? { ...m, content: m.content + delta } : m));
              }
            } catch {}
          }
        }
      }
    } catch {
      setMessages(prev => prev.map(m => m.id === aiMsgId ? { ...m, content: '（AI 顧問暫時無法連線，請稍後再試）' } : m));
    } finally {
      setStreaming(false);
    }
  }

  return (
    <AppChrome title="學習日誌" eyebrow="05.2">
      {/* Summary strip */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 1, background: 'var(--border)', borderBottom: '1px solid var(--border)', marginBottom: 16 }}>
        {[
          { label: '日誌筆數', value: totalTrades, unit: '筆', mono: true },
          { label: '獲利筆數佔比', value: winRate === null ? '—' : winRate.toFixed(1), unit: winRate === null ? '' : '%', mono: true },
          { label: '已寫入規則', value: `${rulesUpdatedCount}`, unit: '條', mono: true },
          { label: '日誌累計損益', value: `${totalPnl >= 0 ? '+' : ''}${totalPnl.toLocaleString()}`, unit: 'NTD', mono: true },
        ].map(kpi => (
          <div key={kpi.label} style={{ background: 'var(--bg)', padding: '12px 16px' }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>{kpi.label}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 700 }}>{kpi.value}</div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 2 }}>{kpi.unit}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 1, background: 'var(--border)', height: 'calc(100vh - 200px)', minHeight: 400 }}>
        {/* Left: Journal entries */}
        <div style={{ flex: '1.6', background: 'var(--bg)', overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
            <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>JOURNAL ENTRIES · {entries.length} 條</span>
            <button style={{ fontSize: 11, padding: '4px 10px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 2, color: 'var(--fg)', cursor: 'pointer' }}>
              匯出 CSV
            </button>
          </div>
          {entries.length === 0
            ? <EmptyHint text="尚無學習日誌" />
            : entries.map(entry => <JournalCard key={entry.id} entry={entry} />)}
        </div>

        {/* Right: AI advisor chat */}
        <div style={{ flex: 1, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1 }}>AI ADVISOR</span>
            <span style={{ fontSize: 9, padding: '2px 6px', background: 'var(--up)', color: '#fff', borderRadius: 2, fontFamily: 'var(--font-mono)' }}>LIVE</span>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
            {messages.length === 0 && !streaming && (
              <EmptyHint text="尚無對話紀錄，開始詢問 AI 顧問" />
            )}
            {messages.map(msg => <ChatBubble key={msg.id} msg={msg} />)}
            {streaming && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <div style={{ width: 28, height: 28, borderRadius: '50%', background: '#333', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: '#fff', flexShrink: 0 }}>AI</div>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 4, padding: '8px 12px' }}>
                  <span style={{ display: 'inline-block', width: 8, height: 14, background: 'var(--fg)', animation: 'blink 1s step-end infinite' }} />
                </div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          <div style={{ padding: 12, borderTop: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
                placeholder="詢問 AI 顧問... (⌘K)"
                style={{ flex: 1, padding: '8px 12px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 2, color: 'var(--fg)', fontSize: 12, outline: 'none', fontFamily: 'inherit' }}
              />
              <button onClick={sendMessage} disabled={streaming || !input.trim()} style={{ padding: '8px 16px', background: streaming ? 'var(--muted)' : 'var(--accent)', border: 'none', borderRadius: 2, color: '#fff', fontSize: 12, cursor: streaming ? 'default' : 'pointer' }}>
                {streaming ? '...' : '送出'}
              </button>
            </div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 6, fontFamily: 'var(--font-mono)' }}>⌘K 快速開啟 · Enter 送出 · Shift+Enter 換行</div>
          </div>
        </div>
      </div>
    </AppChrome>
  );
}
