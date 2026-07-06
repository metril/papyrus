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

interface JobsCache {
  jobs: PrintJob[];
  total: number;
}

interface ScansCache {
  scans: ScanJob[];
  total: number;
}

/** Apply a jobs-channel event (`job_created`/`job_updated`/`job_deleted`). */
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
  }
}

/** Apply a scans-channel event (`scan_completed`/`scan_deleted`), keyed by `scan_id`. */
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
 * `connected` flag into the connection store, and on RECONNECT (not the initial
 * connect) invalidate the channel's keys to recover any events missed while down.
 *
 * `hasConnectedRef` distinguishes the two: the first `connected → true` sets the
 * flag without invalidating; any later `false → true` transition (the ref is
 * already true) is a reconnect and invalidates. A separate mount-only effect
 * resets the ref on unmount so a genuine remount — and StrictMode's simulated
 * unmount/remount — starts fresh and never mistakes the first connect for a
 * reconnect.
 */
function useChannel(
  url: string,
  queryClient: QueryClient,
  applyEvent: (queryClient: QueryClient, msg: WSMessage) => void,
  invalidate: (queryClient: QueryClient) => void,
  setConnected: (connected: boolean) => void,
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
    } else {
      hasConnectedRef.current = true;
    }
  }, [connected, queryClient, invalidate, setConnected]);
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

  useChannel('/api/system/ws/jobs', queryClient, applyJobEvent, invalidateJobs, setJobsConnected);
  useChannel('/api/system/ws/scans', queryClient, applyScanWithToast, invalidateScans, setScansConnected);
  useChannel(
    '/api/system/ws/printers',
    queryClient,
    applyPrinterEvent,
    invalidatePrinters,
    setPrintersConnected,
  );
}
