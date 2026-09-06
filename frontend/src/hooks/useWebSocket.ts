import { useState, useEffect, useRef, useCallback } from 'react';

/** 後端在取不到真實資料時推的 payload，而不是一份假快照。 */
interface WsError {
  error: string;
  detail?: string;
}

function isWsError(v: unknown): v is WsError {
  return !!v && typeof v === 'object' && 'error' in (v as Record<string, unknown>);
}

export function useWebSocket<T>(url: string, onMessage?: (data: T) => void) {
  const [data, setData] = useState<T | null>(null);
  const [wsError, setWsError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const onMessageRef = useRef(onMessage);

  // Keep callback ref fresh without restarting the socket
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    try {
      const wsBase = import.meta.env.VITE_WS_BASE_URL ?? `ws://${window.location.hostname}:1234`;
      // Backend WS endpoints require JWT via ?token= (browsers can't set
      // Authorization headers on WebSocket); unauthenticated sockets close 4401.
      const token = localStorage.getItem('access_token');
      const sep = url.includes('?') ? '&' : '?';
      const fullUrl = token ? `${wsBase}${url}${sep}token=${encodeURIComponent(token)}` : `${wsBase}${url}`;
      const ws = new WebSocket(fullUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (mountedRef.current) setConnected(true);
      };

      ws.onmessage = (e: MessageEvent) => {
        if (!mountedRef.current) return;
        try {
          const parsed: unknown = JSON.parse(e.data);
          // 後端明說「沒有資料」時不要當成快照塞進去——原本 WS 每 30 秒
          // 推一份寫死的假快照，會把 REST 那邊誠實的空狀態蓋掉。
          if (isWsError(parsed)) {
            setWsError(parsed.detail || parsed.error);
            setData(null);
            return;
          }
          setWsError(null);
          setData(parsed as T);
          onMessageRef.current?.(parsed as T);
        } catch {
          // ignore malformed JSON
        }
      };

      ws.onerror = () => {
        // let onclose handle reconnect
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        // Auto-reconnect after 3 seconds
        reconnectTimerRef.current = setTimeout(() => {
          if (mountedRef.current) connect();
        }, 3000);
      };
    } catch {
      // WebSocket constructor can throw in certain environments (e.g. SSR / tests)
      setConnected(false);
      reconnectTimerRef.current = setTimeout(() => {
        if (mountedRef.current) connect();
      }, 3000);
    }
  }, [url]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on deliberate close
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return { data, connected, wsError };
}
