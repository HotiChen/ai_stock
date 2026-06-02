// 05.2 學習日誌 + AI 顧問
import { useState, useRef, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import AppChrome from '../components/AppChrome';
import { api } from '../api';
import type { JournalEntry, ChatMessage } from '../types';

const MOCK_ENTRIES: JournalEntry[] = [
  { id: 1, date: '2026-05-28', code: '2330', name: '台積電', pnl: 8400, lesson: '突破頸線後量能明顯放大，進場時機恰當。應在成交量達均量 1.5 倍以上才考慮追多，本次執行符合規則。', rule_updated: true, tags: ['突破', '量能', '成功'], related_trade_id: 101 },
  { id: 2, date: '2026-05-28', code: '2454', name: '聯發科', pnl: -3200, lesson: '過早停損後股價反彈，停損位設置太緊（2.5%）。AI 信心 0.71 屬中段，應適度放寬停損至 3.5% 或不進場。', rule_updated: true, tags: ['停損', '信心門檻', '檢討'], related_trade_id: 102 },
  { id: 3, date: '2026-05-27', code: '2382', name: '廣達', pnl: 5600, lesson: 'AI 預測信心 0.81，板塊順風，進出場紀律良好。此類高信心 + 板塊強勢組合應優先分配預算。', rule_updated: false, tags: ['高信心', '板塊', '成功'], related_trade_id: 103 },
  { id: 4, date: '2026-05-27', code: '2317', name: '鴻海', pnl: -1800, lesson: '開盤跳空高開後回測，沒有等確認即追買，屬於衝動進場。應等 5 分 K 收盤確認支撐後才進場。', rule_updated: true, tags: ['追買', '衝動', '檢討'], related_trade_id: 104 },
  { id: 5, date: '2026-05-26', code: '2308', name: '台達電', pnl: 12300, lesson: '持有至目標價後分批出場，實現預期報酬率 4.2%。遵守紀律、不貪的典範，AI TP 設置準確。', rule_updated: false, tags: ['目標價', '紀律', '成功'], related_trade_id: 105 },
  { id: 6, date: '2026-05-26', code: '2303', name: '聯電', pnl: -900, lesson: '大盤 TAIEX 下跌 0.8% 但仍進場，未遵守大盤環境過濾規則。需加強市場環境判斷，大盤弱勢時降低交易頻率。', rule_updated: true, tags: ['大盤', '環境', '檢討'], related_trade_id: 106 },
];

const MOCK_CHAT: ChatMessage[] = [
  { id: '1', role: 'user', content: '這週的交易有什麼需要改進的地方？', ts: '2026-05-29T09:15:00' },
  { id: '2', role: 'assistant', content: '根據本週日誌，我觀察到三個模式：\n\n**1. 停損位設置過緊**：聯發科案例顯示 2.5% 停損在波動較大的個股上容易被洗出。建議對高波動股（β > 1.2）放寬至 3.5%。\n\n**2. 大盤過濾不夠嚴格**：聯電交易在大盤下跌 0.8% 時仍進場，這類交易整體勝率偏低。建議加入大盤 > -0.5% 的篩選條件。\n\n**3. 高信心 + 強板塊的組合表現優異**：廣達和台達電都在這個條件下獲利，應增加此類組合的預算比例。', ts: '2026-05-29T09:15:30', model: 'claude-sonnet-4-6' },
  { id: '3', role: 'user', content: '如何調整停損規則？', ts: '2026-05-29T09:16:00' },
  { id: '4', role: 'assistant', content: '建議的停損調整方案：\n\n**基礎停損**：維持 3% 作為預設值\n\n**動態調整**：\n- 信心 ≥ 0.80：可放寬至 3.5%（更多空間讓趨勢發展）\n- 信心 0.65–0.79：維持 3%\n- 信心 < 0.65：收緊至 2.5%（低信心時快速止損）\n\n**板塊加成**：板塊整體強勢時（板塊 change_pct > +1%），可額外放寬 0.5%\n\n這樣的動態停損已寫入 rules.py v47 建議清單，等待您確認後生效。', ts: '2026-05-29T09:16:45', model: 'claude-sonnet-4-6' },
];

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
  const { data: entries = MOCK_ENTRIES } = useQuery({
    queryKey: ['journal'],
    queryFn: () => api.getJournal(),
    retry: false,
  });

  const [messages, setMessages] = useState<ChatMessage[]>(MOCK_CHAT);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const totalTrades = 147;
  const winRate = 63.3;
  const rulesVersion = 47;
  const monthPnl = 89420;

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
          { label: '累計交易', value: totalTrades, unit: '筆', mono: true },
          { label: '整體勝率', value: winRate.toFixed(1), unit: '%', mono: true },
          { label: '學習規則版本', value: `v${rulesVersion}`, unit: 'rules.py', mono: true },
          { label: '本月學習摘要', value: `+${monthPnl.toLocaleString()}`, unit: 'NTD', mono: true },
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
          {entries.map(entry => <JournalCard key={entry.id} entry={entry} />)}
        </div>

        {/* Right: AI advisor chat */}
        <div style={{ flex: 1, background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 10, color: 'var(--muted)', fontFamily: 'var(--font-mono)', letterSpacing: 1 }}>AI ADVISOR</span>
            <span style={{ fontSize: 9, padding: '2px 6px', background: 'var(--up)', color: '#fff', borderRadius: 2, fontFamily: 'var(--font-mono)' }}>LIVE</span>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
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
