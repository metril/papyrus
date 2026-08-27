import { useEffect, useRef, useCallback, useState } from 'react';
import type { WSMessage } from '../types';

interface UseWebSocketOptions {
  url: string | null;
  onMessage?: (message: WSMessage) => void;
  reconnectInterval?: number;
}

// F84: uncapped `reconnectInterval * 2^n` backoff took ~17 minutes to reach
// its last (10th, hard-ceiling) attempt. Capping the delay and dropping the
// ceiling means a socket keeps retrying indefinitely, at most 30s apart.
const MAX_RECONNECT_DELAY_MS = 30_000;

export function useWebSocket({
  url,
  onMessage,
  reconnectInterval = 1000,
}: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectCount = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const onMessageRef = useRef(onMessage);
  // F17: set by effect cleanup right before it intentionally closes the
  // current socket (unmount, or `url`/`reconnectInterval` changing) so that
  // socket's own (later, async) close event knows not to schedule a
  // reconnect. Internal reconnects (scheduled from onclose below) call
  // connectImpl directly, bypassing the effect entirely, so this flag is
  // never involved in the normal reconnect path.
  const closedByUsRef = useRef(false);
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
    closedByUsRef.current = false;
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
      // F17: a superseded socket -- e.g. cleanup closed this one while a
      // rapid `url` change (ScanForm swaps scan ids) opened a new one
      // before this close event fired. wsRef already points at the newer
      // socket, which owns the connected/reconnect state now; this stale
      // event is a no-op regardless of closedByUsRef's value, so it's
      // checked first and unconditionally.
      if (wsRef.current !== ws) return;
      wsRef.current = null;

      // We intentionally closed this via effect cleanup -- never schedule
      // a reconnect for it.
      if (closedByUsRef.current) return;

      setConnected(false);
      const delay = Math.min(
        MAX_RECONNECT_DELAY_MS,
        reconnectInterval * 2 ** reconnectCount.current,
      );
      reconnectCount.current++;
      reconnectTimer.current = setTimeout(connectImpl, delay);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [url, reconnectInterval]);

  useEffect(() => {
    connect();
    return () => {
      closedByUsRef.current = true;
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  // F84: re-arm on wake instead of waiting out the (now uncapped) backoff --
  // a laptop sleeping past the current delay, or a backgrounded tab whose
  // timers get throttled, would otherwise sit disconnected long after the
  // network/tab is actually usable again. Only reconnects when no socket is
  // currently open (wsRef is null, i.e. the previous one already closed and
  // is between reconnect attempts); never interrupts a live or in-flight one.
  useEffect(() => {
    const reconnectIfDown = () => {
      if (wsRef.current) return;
      clearTimeout(reconnectTimer.current);
      connect();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') reconnectIfDown();
    };
    window.addEventListener('online', reconnectIfDown);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      window.removeEventListener('online', reconnectIfDown);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [connect]);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { connected, send };
}
