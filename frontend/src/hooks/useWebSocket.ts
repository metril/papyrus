import { useEffect, useRef, useCallback, useState } from 'react';
import type { WSMessage } from '../types';

interface UseWebSocketOptions {
  url: string | null;
  onMessage?: (message: WSMessage) => void;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
}

export function useWebSocket({
  url,
  onMessage,
  reconnectInterval = 1000,
  maxReconnectAttempts = 10,
}: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectCount = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const onMessageRef = useRef(onMessage);
  const [connected, setConnected] = useState(false);

  // Keep callback ref current without triggering reconnects
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  // Named function expression: the reconnect timer references connectImpl's
  // own persistent binding, so each connect instance reconnects to its own
  // url — a zombie socket's pending reconnect never targets a newer url.
  const connect = useCallback(function connectImpl() {
    if (!url) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}${url}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      setConnected(true);
      reconnectCount.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as WSMessage;
        onMessageRef.current?.(message);
      } catch {
        // Ignore non-JSON messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;

      if (reconnectCount.current < maxReconnectAttempts) {
        const delay = reconnectInterval * Math.pow(2, reconnectCount.current);
        reconnectCount.current++;
        reconnectTimer.current = setTimeout(connectImpl, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [url, reconnectInterval, maxReconnectAttempts]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { connected, send };
}
