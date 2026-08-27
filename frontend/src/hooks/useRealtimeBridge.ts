import { useCallback, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import { useWebSocket } from './useWebSocket';
import { queryKeys } from '../api/queries';
import { useConnectionStore } from '../store/connectionStore';
import { showToast } from '../store/toastStore';
import type { PrintJob, ScanJob, WSMessage } from '../types';

/**
 * WebSocket → Query cache bridge.
 *
 * The backend broadcasts the FULL serialized object on every event, so each
 * event applies surgically to the relevant cache entry via `setQueryData` — we
 * never refetch per event. The one exception is `printer_status`, which carries
 * a status blob that can affect both the printer-status query and the managed
 * printer rows, so it invalidates both keys instead.
 *
 * CRITICAL: when a cache entry doesn't exist yet (the page was never visited, so
 * `getQueryData` is undefined) the updater returns `undefined`. In TanStack
 * Query v5 a functional updater that resolves to `undefined` is a no-op — no
 * query entry is created — so we never seed a partial list.
 */

// Hoisted once at module scope: `queryKeys.jobs.list()`/`.scans.list()` are
// factory functions that return a *new* array on every call. Used directly
// as a `useEffect` dependency below (freshnessKey), a fresh reference every
// render would make that effect re-run — and re-invalidate — on every
// render once `hasConnectedRef` is already true, not just on `connected`
// changes. `queryKeys.printerStatus` needs no such hoisting: it's a plain
// `as const` array property, already one stable reference.
const JOBS_LIST_KEY = queryKeys.jobs.list();
const SCANS_LIST_KEY = queryKeys.scans.list();

interface JobsCache {
  jobs: PrintJob[];
  total: number;
}

interface ScansCache {
  scans: ScanJob[];
  total: number;
}

/**
 * Apply a jobs-channel event (`job_created`/`job_updated`/`job_deleted`).
 *
 * The live queue list (`queryKeys.jobs.list()`) is upserted surgically via
 * `setQueryData`, same as always. History's own paginated cache
 * (`queryKeys.jobs.history(page)`) can't be upserted the same way — a new or
 * deleted job shifts every later page's offset — so instead every event
 * invalidates the whole `historyAll` prefix (F81), which is a no-op unless
 * HistoryPage is actually mounted with pages cached.
 */
export function applyJobEvent(queryClient: QueryClient, msg: WSMessage): void {
  const key = queryKeys.jobs.list();

  if (msg.type === 'job_created' || msg.type === 'job_updated') {
    const incoming = msg.data as unknown as PrintJob;
    queryClient.setQueryData<JobsCache>(key, (prev) => {
      if (!prev) return undefined;
      const exists = prev.jobs.some((j) => j.id === incoming.id);
      if (exists) {
        // Replace in place — preserve ordering, total unchanged.
        return {
          ...prev,
          jobs: prev.jobs.map((j) => (j.id === incoming.id ? incoming : j)),
        };
      }
      // Unseen job: prepend (list is newest-first) and grow total.
      return { jobs: [incoming, ...prev.jobs], total: prev.total + 1 };
    });
    queryClient.invalidateQueries({ queryKey: queryKeys.jobs.historyAll });
    return;
  }

  if (msg.type === 'job_deleted') {
    const id = (msg.data as { id?: number }).id;
    if (typeof id !== 'number') return;
    queryClient.setQueryData<JobsCache>(key, (prev) => {
      if (!prev) return undefined;
      const exists = prev.jobs.some((j) => j.id === id);
      // Only decrement total when the row was actually present.
      if (!exists) return prev;
      return { jobs: prev.jobs.filter((j) => j.id !== id), total: prev.total - 1 };
    });
    queryClient.invalidateQueries({ queryKey: queryKeys.jobs.historyAll });
  }
}

/**
 * Apply a scans-channel event (`scan_completed`/`scan_deleted`), keyed by
 * `scan_id`. See applyJobEvent above for why history's pages are
 * invalidated (F81) rather than upserted like the live list.
 */
export function applyScanEvent(queryClient: QueryClient, msg: WSMessage): void {
  const key = queryKeys.scans.list();

  if (msg.type === 'scan_completed') {
    const incoming = msg.data as unknown as ScanJob;
    queryClient.setQueryData<ScansCache>(key, (prev) => {
      if (!prev) return undefined;
      const exists = prev.scans.some((s) => s.scan_id === incoming.scan_id);
      if (exists) {
        return {
          ...prev,
          scans: prev.scans.map((s) => (s.scan_id === incoming.scan_id ? incoming : s)),
        };
      }
      return { scans: [incoming, ...prev.scans], total: prev.total + 1 };
    });
    queryClient.invalidateQueries({ queryKey: queryKeys.scans.historyAll });
    return;
  }

  if (msg.type === 'scan_deleted') {
    const scanId = (msg.data as { scan_id?: string }).scan_id;
    if (typeof scanId !== 'string') return;
    queryClient.setQueryData<ScansCache>(key, (prev) => {
      if (!prev) return undefined;
      const exists = prev.scans.some((s) => s.scan_id === scanId);
      if (!exists) return prev;
      return { scans: prev.scans.filter((s) => s.scan_id !== scanId), total: prev.total - 1 };
    });
    queryClient.invalidateQueries({ queryKey: queryKeys.scans.historyAll });
  }
}

/** Apply a printers-channel event (`printer_status`). Invalidates both status keys. */
export function applyPrinterEvent(queryClient: QueryClient, msg: WSMessage): void {
  if (msg.type === 'printer_status') {
    invalidatePrinters(queryClient);
  }
}

function invalidateJobs(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
}

function invalidateScans(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: queryKeys.scans.list() });
}

function invalidatePrinters(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: queryKeys.printerStatus });
  queryClient.invalidateQueries({ queryKey: queryKeys.printers.list() });
}

/**
 * Wire a single WS channel: dispatch its events to the cache, mirror its
 * `connected` flag into the connection store, and invalidate the channel's
 * keys to recover any events missed while the socket wasn't listening.
 *
 * `hasConnectedRef` distinguishes the first connect from a later reconnect:
 * the first `connected → true` doesn't unconditionally invalidate (see
 * F144 below); any later `false → true` transition (the ref is already
 * true) is a reconnect and always invalidates, to recover events missed
 * while the socket was down. A separate mount-only effect resets the ref on
 * unmount so a genuine remount — and StrictMode's simulated unmount/remount
 * — starts fresh and never mistakes the first connect for a reconnect.
 *
 * F144: the initial list fetch and the socket handshake start concurrently
 * at mount, and the socket can win or lose that race. If it loses (opens
 * *after* the fetch resolved), an event broadcast in between reaches no
 * listener and is silently dropped — e.g. GET /api/jobs resolves at t=80ms,
 * a job is created at t=100ms, and ws.onopen doesn't fire until t=140ms.
 * On the first `connected → true`, `freshnessKey`'s `dataUpdatedAt` is
 * compared against the socket-open time: if the cached data predates the
 * socket opening, we lost that race and invalidate once to reconcile;
 * data fetched at/after the socket opened (or never fetched at all, so
 * there's nothing to reconcile) skips the redundant invalidate.
 */
function useChannel(
  url: string,
  queryClient: QueryClient,
  applyEvent: (queryClient: QueryClient, msg: WSMessage) => void,
  invalidate: (queryClient: QueryClient) => void,
  setConnected: (connected: boolean) => void,
  freshnessKey: readonly unknown[],
): void {
  const onMessage = useCallback(
    (msg: WSMessage) => applyEvent(queryClient, msg),
    [applyEvent, queryClient],
  );

  const { connected } = useWebSocket({ url, onMessage });

  const hasConnectedRef = useRef(false);

  // Reset only on real unmount / StrictMode remount, never on a connection blip.
  useEffect(() => {
    return () => {
      hasConnectedRef.current = false;
    };
  }, []);

  useEffect(() => {
    setConnected(connected);
    if (!connected) return;

    if (hasConnectedRef.current) {
      invalidate(queryClient);
      return;
    }
    hasConnectedRef.current = true;

    const openedAt = Date.now();
    const dataUpdatedAt = queryClient.getQueryState(freshnessKey)?.dataUpdatedAt;
    if (dataUpdatedAt !== undefined && dataUpdatedAt < openedAt) {
      invalidate(queryClient);
    }
  }, [connected, queryClient, invalidate, setConnected, freshnessKey]);
}

/**
 * Opens the three realtime channels and keeps the Query cache live app-wide.
 * Mount it once, inside the authenticated branch of the app shell.
 */
export function useRealtimeBridge(): void {
  const queryClient = useQueryClient();
  const setJobsConnected = useConnectionStore((s) => s.setJobsConnected);
  const setScansConnected = useConnectionStore((s) => s.setScansConnected);
  const setPrintersConnected = useConnectionStore((s) => s.setPrintersConnected);

  // Scans channel: apply the cache update, then surface the app-wide "Scan
  // completed" toast (previously wired directly in AppShell).
  const applyScanWithToast = useCallback((qc: QueryClient, msg: WSMessage) => {
    applyScanEvent(qc, msg);
    if (msg.type === 'scan_completed') showToast('Scan completed', 'success');
  }, []);

  useChannel(
    '/api/system/ws/jobs',
    queryClient,
    applyJobEvent,
    invalidateJobs,
    setJobsConnected,
    JOBS_LIST_KEY,
  );
  useChannel(
    '/api/system/ws/scans',
    queryClient,
    applyScanWithToast,
    invalidateScans,
    setScansConnected,
    SCANS_LIST_KEY,
  );
  useChannel(
    '/api/system/ws/printers',
    queryClient,
    applyPrinterEvent,
    invalidatePrinters,
    setPrintersConnected,
    queryKeys.printerStatus,
  );
}
