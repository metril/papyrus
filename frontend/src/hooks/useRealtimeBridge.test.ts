import { describe, it, expect, beforeEach } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { applyJobEvent, applyScanEvent, applyPrinterEvent } from './useRealtimeBridge';
import { queryKeys } from '../api/queries';
import type { PrintJob, ScanJob, WSMessage } from '../types';

function makeJob(id: number, overrides: Partial<PrintJob> = {}): PrintJob {
  return {
    id,
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
