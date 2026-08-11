/**
 * 空狀態提示 — 全站共用。
 *
 * 存在的理由：這是一個真實下單的台股交易系統。過去每個頁面在 API 沒有資料時
 * 會靜默退回寫死的 MOCK_* 常數，畫面照樣列出「2330 台積電 信心 0.86 買進」，
 * 但那只是示範資料，不是任何一次真實預測或真實持倉。使用者無從分辨真假，
 * 可能照著捏造的訊號下單。
 *
 * 原則：寧可顯示空白（這個元件），也不要顯示看起來合理的假資料。
 * 從 Dashboard.tsx 抽出，供所有頁面共用同一套空狀態視覺語言。
 */
export default function EmptyHint({ text }: { text: string }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '100%',
      minHeight: 80,
      padding: '20px 12px',
      color: 'var(--ink-3, #888)',
      fontSize: 12,
      letterSpacing: '0.02em',
      textAlign: 'center',
    }}>
      {text}
    </div>
  );
}
