import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import type { ReactNode } from 'react';
import {
  applyJobEvent,
  applyScanEvent,
  applyPrinterEvent,
  useRealtimeBridge,
} from './useRealtimeBridge';
import { queryKeys } from '../api/queries';
import { useConnectionStore } from '../store/connectionStore';
import type { PrintJob, ScanJob, WSMessage } from '../types';

function makeJob(id: number, overrides: Partial<PrintJob> = {}): PrintJob {
  return {
    id,
    user_id: null,
    cups_job_id: null,
    title: `Job ${id}`,
    filename: `job-${id}.pdf`,
    file_size: 1024,
    mime_type: 'application/pdf',
    status: 'held',
    copies: 1,
    duplex: false,
    media: 'A4',
    source_type: 'upload',
    printer_id: null,
    has_pin: false,
    error_message: null,
    created_at: '2026-07-05T00:00:00Z',
    updated_at: '2026-07-05T00:00:00Z',
    completed_at: null,
    ...overrides,
  };
}

function makeScan(scanId: string, overrides: Partial<ScanJob> = {}): ScanJob {
  return {
    id: 1,
    scan_id: scanId,
    status: 'completed',
    resolution: 300,
    mode: 'Color',
    format: 'pdf',
    source: 'Flatbed',
    page_count: 1,
    file_size: 2048,
    error_message: null,
    created_at: '2026-07-05T00:00:00Z',
    completed_at: '2026-07-05T00:00:00Z',
    ...overrides,
  };
}

function msg(type: string, data: unknown): WSMessage {
  return { type, data: data as Record<string, unknown> };
}

interface JobsCache {
  jobs: PrintJob[];
  total: number;
}
interface ScansCache {
  scans: ScanJob[];
  total: number;
}

describe('applyJobEvent', () => {
  let qc: QueryClient;
  const key = queryKeys.jobs.list();

  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  it('job_created prepends onto an existing cache and increments total', () => {
    qc.setQueryData<JobsCache>(key, { jobs: [], total: 0 });

    applyJobEvent(qc, msg('job_created', makeJob(1)));

    const cache = qc.getQueryData<JobsCache>(key);
    expect(cache).toEqual({ jobs: [makeJob(1)], total: 1 });
  });

  it('job_created prepends a newer job ahead of existing ones', () => {
    qc.setQueryData<JobsCache>(key, { jobs: [makeJob(1)], total: 1 });

    applyJobEvent(qc, msg('job_created', makeJob(2)));

    const cache = qc.getQueryData<JobsCache>(key)!;
    expect(cache.jobs.map((j) => j.id)).toEqual([2, 1]);
    expect(cache.total).toBe(2);
  });

  it('job_updated replaces in place without reordering or changing total', () => {
    qc.setQueryData<JobsCache>(key, { jobs: [makeJob(1), makeJob(2)], total: 2 });

    applyJobEvent(qc, msg('job_updated', makeJob(2, { status: 'printing' })));

    const cache = qc.getQueryData<JobsCache>(key)!;
    expect(cache.jobs.map((j) => j.id)).toEqual([1, 2]);
    expect(cache.jobs[1].status).toBe('printing');
    expect(cache.total).toBe(2);
  });

  it('job_deleted removes the row and decrements total', () => {
    qc.setQueryData<JobsCache>(key, { jobs: [makeJob(1), makeJob(2)], total: 2 });

    applyJobEvent(qc, msg('job_deleted', { id: 1 }));

    const cache = qc.getQueryData<JobsCache>(key)!;
    expect(cache.jobs.map((j) => j.id)).toEqual([2]);
    expect(cache.total).toBe(1);
  });

  it('job_deleted for an id not in the cache leaves total unchanged', () => {
    qc.setQueryData<JobsCache>(key, { jobs: [makeJob(1)], total: 1 });

    applyJobEvent(qc, msg('job_deleted', { id: 999 }));

    const cache = qc.getQueryData<JobsCache>(key)!;
    expect(cache.jobs.map((j) => j.id)).toEqual([1]);
    expect(cache.total).toBe(1);
  });

  // F81: history's own paginated cache can't be upserted like the live list
  // (a new/deleted job shifts every later page's offset), so every job
  // event invalidates the whole historyAll prefix instead.
  it('job_created also invalidates any cached jobs-history pages', () => {
    qc.setQueryData(key, { jobs: [], total: 0 });
    qc.setQueryData(queryKeys.jobs.history(0), { jobs: [makeJob(1)], total: 1 });

    applyJobEvent(qc, msg('job_created', makeJob(2)));

    expect(qc.getQueryState(queryKeys.jobs.history(0))?.isInvalidated).toBe(true);
  });

  it('job_deleted also invalidates any cached jobs-history pages', () => {
    qc.setQueryData(key, { jobs: [makeJob(1)], total: 1 });
    qc.setQueryData(queryKeys.jobs.history(0), { jobs: [makeJob(1)], total: 1 });

    applyJobEvent(qc, msg('job_deleted', { id: 1 }));

    expect(qc.getQueryState(queryKeys.jobs.history(0))?.isInvalidated).toBe(true);
  });

  it('leaves the cache unset for every job event when the key was never seeded', () => {
    for (const event of [
      msg('job_created', makeJob(1)),
      msg('job_updated', makeJob(1)),
      msg('job_deleted', { id: 1 }),
    ]) {
      applyJobEvent(qc, event);
      expect(qc.getQueryData<JobsCache>(key)).toBeUndefined();
      expect(qc.getQueryCache().find({ queryKey: key })).toBeUndefined();
    }
  });
});

describe('applyScanEvent', () => {
  let qc: QueryClient;
  const key = queryKeys.scans.list();

  beforeEach(() => {
    qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  it('scan_completed inserts an unseen scan and increments total', () => {
    qc.setQueryData<ScansCache>(key, { scans: [], total: 0 });

    applyScanEvent(qc, msg('scan_completed', makeScan('abc')));

    const cache = qc.getQueryData<ScansCache>(key)!;
    expect(cache.scans.map((s) => s.scan_id)).toEqual(['abc']);
    expect(cache.total).toBe(1);
  });

  it('scan_completed upserts by scan_id in place', () => {
    qc.setQueryData<ScansCache>(key, {
      scans: [makeScan('abc'), makeScan('def')],
      total: 2,
    });

    applyScanEvent(qc, msg('scan_completed', makeScan('def', { page_count: 5 })));

    const cache = qc.getQueryData<ScansCache>(key)!;
    expect(cache.scans.map((s) => s.scan_id)).toEqual(['abc', 'def']);
    expect(cache.scans[1].page_count).toBe(5);
    expect(cache.total).toBe(2);
  });

  it('scan_deleted removes by scan_id and decrements total', () => {
    qc.setQueryData<ScansCache>(key, {
      scans: [makeScan('abc'), makeScan('def')],
      total: 2,
    });

    applyScanEvent(qc, msg('scan_deleted', { scan_id: 'abc' }));

    const cache = qc.getQueryData<ScansCache>(key)!;
    expect(cache.scans.map((s) => s.scan_id)).toEqual(['def']);
    expect(cache.total).toBe(1);
  });

  it('scan_completed also invalidates any cached scans-history pages', () => {
    qc.setQueryData(key, { scans: [], total: 0 });
    qc.setQueryData(queryKeys.scans.history(0), { scans: [makeScan('abc')], total: 1 });

    applyScanEvent(qc, msg('scan_completed', makeScan('def')));

    expect(qc.getQueryState(queryKeys.scans.history(0))?.isInvalidated).toBe(true);
  });

  it('scan_deleted also invalidates any cached scans-history pages', () => {
    qc.setQueryData(key, { scans: [makeScan('abc')], total: 1 });
    qc.setQueryData(queryKeys.scans.history(0), { scans: [makeScan('abc')], total: 1 });

    applyScanEvent(qc, msg('scan_deleted', { scan_id: 'abc' }));

    expect(qc.getQueryState(queryKeys.scans.history(0))?.isInvalidated).toBe(true);
  });

  it('leaves the cache unset for scan events when the key was never seeded', () => {
    applyScanEvent(qc, msg('scan_completed', makeScan('abc')));
    expect(qc.getQueryData<ScansCache>(key)).toBeUndefined();

    applyScanEvent(qc, msg('scan_deleted', { scan_id: 'abc' }));
    expect(qc.getQueryData<ScansCache>(key)).toBeUndefined();
    expect(qc.getQueryCache().find({ queryKey: key })).toBeUndefined();
  });
});

describe('applyPrinterEvent', () => {
  it('printer_status invalidates both the printerStatus and printers.list keys', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(queryKeys.printerStatus, { state: 3, state_message: '', accepting_jobs: true });
    qc.setQueryData(queryKeys.printers.list(), []);

    // Sanity: freshly seeded queries are not invalidated.
    expect(qc.getQueryState(queryKeys.printerStatus)?.isInvalidated).toBe(false);
    expect(qc.getQueryState(queryKeys.printers.list())?.isInvalidated).toBe(false);

    applyPrinterEvent(qc, msg('printer_status', { state: 4 }));

    expect(qc.getQueryState(queryKeys.printerStatus)?.isInvalidated).toBe(true);
    expect(qc.getQueryState(queryKeys.printers.list())?.isInvalidated).toBe(true);
  });

  it('ignores non printer_status events', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(queryKeys.printerStatus, { state: 3, state_message: '', accepting_jobs: true });

    applyPrinterEvent(qc, msg('something_else', {}));

    expect(qc.getQueryState(queryKeys.printerStatus)?.isInvalidated).toBe(false);
  });
});

// --- F144: invalidate-once on the first connect when the fetch/socket race is lost ---

/**
 * Minimal mock of the browser WebSocket, driven manually via
 * `simulateOpen`/`simulateClose` -- see useWebSocket.test.ts for the same
 * pattern in more detail. `useRealtimeBridge` opens three sockets at once
 * (jobs/scans/printers); tests below pick the one they care about by its
 * url substring.
 */
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  simulateOpen() {
    this.readyState = 1;
    this.onopen?.();
  }
}

function socketFor(urlSubstring: string): MockWebSocket {
  const ws = MockWebSocket.instances.find((s) => s.url.includes(urlSubstring));
  if (!ws) throw new Error(`no mock socket opened for ${urlSubstring}`);
  return ws;
}

function makeWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

describe('useRealtimeBridge (F144: first-connect invalidation)', () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
    useConnectionStore.setState({
      jobsConnected: false,
      scansConnected: false,
      printersConnected: false,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('invalidates once when cached data predates the socket opening (missed-event race)', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // Simulates GET /api/jobs resolving before the socket opens: a
    // job_created broadcast in between would have reached no listener.
    qc.setQueryData(queryKeys.jobs.list(), { jobs: [], total: 0 }, { updatedAt: Date.now() - 10_000 });

    renderHook(() => useRealtimeBridge(), { wrapper: makeWrapper(qc) });
    expect(qc.getQueryState(queryKeys.jobs.list())?.isInvalidated).toBe(false);

    act(() => socketFor('/api/system/ws/jobs').simulateOpen());

    expect(qc.getQueryState(queryKeys.jobs.list())?.isInvalidated).toBe(true);
  });

  it('does not invalidate when cached data is at least as new as the socket open (no race lost)', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // Fetch "resolves" after the socket will open -- nothing was missed.
    qc.setQueryData(queryKeys.jobs.list(), { jobs: [], total: 0 }, { updatedAt: Date.now() + 10_000 });

    renderHook(() => useRealtimeBridge(), { wrapper: makeWrapper(qc) });
    act(() => socketFor('/api/system/ws/jobs').simulateOpen());

    expect(qc.getQueryState(queryKeys.jobs.list())?.isInvalidated).toBe(false);
  });

  it('does not invalidate or seed the cache when the query was never fetched', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    renderHook(() => useRealtimeBridge(), { wrapper: makeWrapper(qc) });
    act(() => socketFor('/api/system/ws/jobs').simulateOpen());

    expect(qc.getQueryCache().find({ queryKey: queryKeys.jobs.list() })).toBeUndefined();
  });

  it('still invalidates on a genuine reconnect (second connect), regardless of freshness', () => {
    vi.useFakeTimers();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // Fresh data -- the first-connect freshness check alone would skip
    // invalidating, so a positive result here can only come from the
    // reconnect branch.
    qc.setQueryData(queryKeys.jobs.list(), { jobs: [], total: 0 }, { updatedAt: Date.now() + 10_000 });

    renderHook(() => useRealtimeBridge(), { wrapper: makeWrapper(qc) });
    const first = socketFor('/api/system/ws/jobs');
    act(() => first.simulateOpen());
    expect(qc.getQueryState(queryKeys.jobs.list())?.isInvalidated).toBe(false);

    // A real disconnect (server/network drops the socket, not our cleanup):
    // useWebSocket's own reconnect logic schedules and opens a new socket
    // after the default 1s backoff.
    const before = MockWebSocket.instances.length;
    act(() => first.onclose?.());
    act(() => vi.advanceTimersByTime(1000));
    expect(MockWebSocket.instances.length).toBe(before + 1);

    const second = MockWebSocket.instances[MockWebSocket.instances.length - 1];
    act(() => second.simulateOpen());

    // Second connected->true is a reconnect (hasConnectedRef already true
    // from the first) -- always invalidates, unlike the first connect's
    // freshness-gated check.
    expect(qc.getQueryState(queryKeys.jobs.list())?.isInvalidated).toBe(true);

    vi.useRealTimers();
  });
});
