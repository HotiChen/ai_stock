import React from 'react';

/**
 * 空狀態 / 錯誤狀態。
 *
 * 為什麼需要這個元件
 * ------------------
 * 這些頁面原本在 API 拿不到資料時 fallback 到寫死的 mock：
 *
 *   const pnl = liveData?.net_pnl ?? 12340;          // 憑空生出 +12,340 獲利
 *   const { data: report = MOCK_REPORT } = useQuery(...)
 *
 * 畫面因此永遠「看起來正常」，而且沒有任何方法能從畫面上分辨哪個數字是真的。
 * 這與後端反覆出現的失敗模式是同一類：chip_data 網路失敗回 {} 看起來像
 * 「今天沒有法人買賣超」、notice 端點沒帶日期回 0 列看起來像「今天沒有
 * 注意股」。假的綠燈比紅燈危險。
 *
 * 規則：沒有資料就說沒有資料，連不上就說連不上，並指出該去看哪裡。
 */

interface Props {
  /** 標題，一句話說明目前的狀態 */
  title: string;
  /** 補充說明：該怎麼辦、去哪裡看 */
  detail?: string;
  /** true = 連線／伺服器錯誤（紅），false = 單純沒有資料（灰） */
  isError?: boolean;
  /** 讓呼叫端塞重試按鈕之類的動作 */
  children?: React.ReactNode;
  /** 嵌在小卡片裡時用 compact，減少留白 */
  compact?: boolean;
}

export default function DataState({
  title, detail, isError = false, children, compact = false,
}: Props) {
  return (
    <div
      role="status"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        padding: compact ? '20px 16px' : '48px 24px',
        minHeight: compact ? 0 : 160,
        background: 'var(--surface, var(--bg))',
        border: '1px solid var(--hair, var(--border))',
        borderLeft: `2px solid ${isError ? 'var(--up)' : 'var(--hair, var(--border))'}`,
        textAlign: 'center',
      }}
    >
      <div
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 10,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          color: isError ? 'var(--up)' : 'var(--muted)',
        }}
      >
        {isError ? 'NO CONNECTION' : 'NO DATA'}
      </div>

      <div style={{ fontSize: 13, color: 'var(--fg, var(--ink))', fontWeight: 500 }}>
        {title}
      </div>

      {detail && (
        <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.6, maxWidth: 380 }}>
          {detail}
        </div>
      )}

      {children}
    </div>
  );
}

/**
 * 把 react-query 的三種狀態收斂成一個決定。
 *
 * 回傳 null 代表「有資料，正常渲染」；回傳元素代表「該顯示狀態畫面」。
 * 呼叫端一律寫成：
 *
 *   const state = queryState({ isLoading, isError, error, data, empty: !rows.length });
 *   if (state) return <AppChrome ...>{state}</AppChrome>;
 */
export function queryState(opts: {
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  isEmpty?: boolean;
  /** 這一頁在講什麼，用於文案，例如 "週報" */
  what: string;
  /** 沒資料時的補充說明，例如 "13:35 收盤複盤後才會產生" */
  emptyDetail?: string;
  compact?: boolean;
}): React.ReactElement | null {
  const { isLoading, isError, error, isEmpty, what, emptyDetail, compact } = opts;

  if (isLoading) {
    return (
      <DataState
        title={`載入${what}…`}
        compact={compact}
      />
    );
  }

  if (isError) {
    const msg = error instanceof Error ? error.message : String(error ?? '');
    return (
      <DataState
        isError
        title={`無法取得${what}`}
        detail={
          (msg ? `${msg.slice(0, 160)}　` : '') +
          '後端 :1234 是否在執行？bash start_all.sh 可重新啟動，logs/backend.log 有詳細錯誤。'
        }
        compact={compact}
      />
    );
  }

  if (isEmpty) {
    return (
      <DataState
        title={`目前沒有${what}`}
        detail={emptyDetail}
        compact={compact}
      />
    );
  }

  return null;
}
