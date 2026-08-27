import { useState, useMemo, useCallback } from 'react';
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import { History, SearchX, Trash2 } from 'lucide-react';
import { queryKeys, HISTORY_PAGE_SIZE } from '../api/queries';
import { deleteJob, bulkDeleteJobs, listJobs } from '../api/printer';
import {
  deleteScan,
  bulkDeleteScans,
  listScans,
  getScanDownloadUrl,
  getJobDownloadUrl,
  getJobPreviewUrl,
} from '../api/scanner';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import FilePreviewModal from '../components/common/FilePreviewModal';
import Skeleton from '../components/common/Skeleton';
import EmptyState from '../components/common/EmptyState';
import ErrorState from '../components/common/ErrorState';
import HistoryRow from '../components/history/HistoryRow';
import type { PrintJob, ScanJob } from '../types';

type Tab = 'all' | 'print' | 'scan';
type StatusFilter = 'all' | 'completed' | 'failed' | 'held' | 'scanning';
type DateFilter = 'all' | 'today' | 'week' | 'month';

export interface HistoryItem {
  type: 'print' | 'scan';
  id: string;
  numericId: number;
  scanId?: string;
  label: string;
  status: string;
  time: string;
  detail: string;
  downloadUrl: string;
  previewUrl?: string;
  mimeType: string;
  filename: string;
  raw: PrintJob | ScanJob;
}

function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function scanMimeType(scan: ScanJob): string {
  if (scan.format === 'pdf') return 'application/pdf';
  return `image/${scan.format}`;
}

function isWithinDate(timeStr: string, filter: DateFilter): boolean {
  if (filter === 'all') return true;
  const date = new Date(timeStr);
  const now = new Date();
  if (filter === 'today') {
    return date.toDateString() === now.toDateString();
  }
  if (filter === 'week') {
    const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    return date >= weekAgo;
  }
  if (filter === 'month') {
    const monthAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    return date >= monthAgo;
  }
  return true;
}

export default function HistoryPage() {
  const queryClient = useQueryClient();

  // F81: History used to render straight off the same capped, unfiltered
  // useJobs()/useScans() the live Print Queue uses (backend default
  // limit=50), so it silently omitted everything past the 50 newest
  // jobs/scans of any status while still labeling itself "All time" with an
  // item count. It now owns a real paginated cache: each page is its own
  // query (queryKeys.jobs/scans.history(page)), and "Load more" fetches one
  // more page — already-loaded pages stay cached and are just concatenated.
  const [pageCount, setPageCount] = useState(1);
  const pageIndexes = useMemo(() => Array.from({ length: pageCount }, (_, i) => i), [pageCount]);

  // Review fix: `useQueries` with no `combine` returns a freshly-mapped
  // array on every render — `jobPages`/`scanPages` (and anything derived
  // from them) changed identity on every render regardless of whether the
  // underlying data actually changed, busting the `items`/`filtered`
  // useMemos below and, with them, HistoryRow's React.memo (every row —
  // thumbnails included — re-rendered on every checkbox click). `combine`
  // is memoized internally via a deep-equal structural-sharing check, so
  // `jobs`/`scans`/`total` below keep the same reference across renders
  // whose underlying query data hasn't changed.
  const jobsHistory = useQueries({
    queries: pageIndexes.map((page) => ({
      queryKey: queryKeys.jobs.history(page),
      queryFn: () => listJobs({ limit: HISTORY_PAGE_SIZE, offset: page * HISTORY_PAGE_SIZE }),
    })),
    combine: (results) => ({
      jobs: results.flatMap((r) => r.data?.jobs ?? []),
      total: results[0]?.data?.total ?? 0,
      isPending: results[0]?.isPending ?? false,
      isError: results.some((r) => r.isError),
      isFetching: results.some((r) => r.isFetching),
    }),
  });
  const scansHistory = useQueries({
    queries: pageIndexes.map((page) => ({
      queryKey: queryKeys.scans.history(page),
      queryFn: () => listScans({ limit: HISTORY_PAGE_SIZE, offset: page * HISTORY_PAGE_SIZE }),
    })),
    combine: (results) => ({
      scans: results.flatMap((r) => r.data?.scans ?? []),
      total: results[0]?.data?.total ?? 0,
      isPending: results[0]?.isPending ?? false,
      isError: results.some((r) => r.isError),
      isFetching: results.some((r) => r.isFetching),
    }),
  });

  const jobs = jobsHistory.jobs;
  const scans = scansHistory.scans;
  const canLoadMore = jobs.length < jobsHistory.total || scans.length < scansHistory.total;
  const loadingMore = pageCount > 1 && (jobsHistory.isFetching || scansHistory.isFetching);

  const loading = jobsHistory.isPending || scansHistory.isPending;
  const hasError = jobsHistory.isError || scansHistory.isError;
  // Refetch by key prefix (not by iterating stored `.refetch` closures —
  // those aren't part of the memoized `combine` output above, and stuffing
  // a fresh function into it on every render would itself defeat combine's
  // structural-sharing memoization) — matches the invalidation pattern the
  // realtime bridge already uses for these same `historyAll` prefixes.
  const refetchAll = () => {
    queryClient.refetchQueries({ queryKey: queryKeys.jobs.historyAll });
    queryClient.refetchQueries({ queryKey: queryKeys.scans.historyAll });
  };

  const [tab, setTab] = useState<Tab>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [dateFilter, setDateFilter] = useState<DateFilter>('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [previewItem, setPreviewItem] = useState<HistoryItem | null>(null);

  // F147: pruning the deleted id out of `selected` here (not just
  // invalidating the list) keeps the "N selected" bar from showing a phantom
  // count for a row that's already gone.
  const deleteJobMutation = useMutation({
    mutationFn: (jobId: number) => deleteJob(jobId),
    onSuccess: (_result, jobId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.jobs.historyAll });
      setSelected((prev) => {
        if (!prev.has(`print-${jobId}`)) return prev;
        const next = new Set(prev);
        next.delete(`print-${jobId}`);
        return next;
      });
    },
  });

  const deleteScanMutation = useMutation({
    mutationFn: (scanId: string) => deleteScan(scanId),
    onSuccess: (_result, scanId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scans.historyAll });
      setSelected((prev) => {
        if (!prev.has(`scan-${scanId}`)) return prev;
        const next = new Set(prev);
        next.delete(`scan-${scanId}`);
        return next;
      });
    },
  });

  const bulkDeleteMutation = useMutation({
    mutationFn: async ({ printIds, scanIds }: { printIds: number[]; scanIds: string[] }) => {
      const promises: Promise<unknown>[] = [];
      if (printIds.length > 0) promises.push(bulkDeleteJobs(printIds));
      if (scanIds.length > 0) promises.push(bulkDeleteScans(scanIds));
      await Promise.all(promises);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.jobs.historyAll });
      queryClient.invalidateQueries({ queryKey: queryKeys.scans.historyAll });
      setSelected(new Set());
    },
  });

  // Build unified history items
  const items = useMemo<HistoryItem[]>(() => {
    const printItems: HistoryItem[] = jobs.map((j) => ({
      type: 'print',
      id: `print-${j.id}`,
      numericId: j.id,
      label: j.filename,
      status: j.status,
      time: j.created_at,
      detail: `${j.copies} cop${j.copies > 1 ? 'ies' : 'y'} · ${j.media}${j.duplex ? ' · Duplex' : ''}${j.file_size ? ` · ${formatSize(j.file_size)}` : ''}`,
      downloadUrl: getJobDownloadUrl(j.id),
      previewUrl: getJobPreviewUrl(j.id),
      mimeType: j.mime_type,
      filename: j.filename,
      raw: j,
    }));

    const scanItems: HistoryItem[] = scans.map((s) => ({
      type: 'scan',
      id: `scan-${s.scan_id}`,
      numericId: s.id,
      scanId: s.scan_id,
      label: `${s.format.toUpperCase()} ${s.resolution} DPI`,
      status: s.status,
      time: s.created_at,
      detail: `${s.mode} · ${s.source}${s.page_count > 1 ? ` · ${s.page_count} pages` : ''}${s.file_size ? ` · ${formatSize(s.file_size)}` : ''}`,
      downloadUrl: getScanDownloadUrl(s.scan_id),
      mimeType: scanMimeType(s),
      filename: `scan_${s.scan_id}.${s.format}`,
      raw: s,
    }));

    return [...printItems, ...scanItems].sort(
      (a, b) => new Date(b.time).getTime() - new Date(a.time).getTime()
    );
  }, [jobs, scans]);

  // Apply filters
  const filtered = useMemo(() => {
    let result = items;
    if (tab !== 'all') result = result.filter((i) => i.type === tab);
    if (statusFilter !== 'all') result = result.filter((i) => i.status === statusFilter);
    if (dateFilter !== 'all') result = result.filter((i) => isWithinDate(i.time, dateFilter));
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (i) => i.label.toLowerCase().includes(q) || i.detail.toLowerCase().includes(q)
      );
    }
    return result;
  }, [items, tab, statusFilter, dateFilter, search]);

  // Selection helpers
  const allSelected = filtered.length > 0 && filtered.every((i) => selected.has(i.id));
  const someSelected = selected.size > 0;

  // Stable identity + id-based signature: toggling one row's checkbox must
  // not recreate a callback that would defeat HistoryRow's memoization for
  // every other row.
  const toggleSelect = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filtered.map((i) => i.id)));
    }
  };

  const handleBulkDelete = () => {
    if (!someSelected) return;

    const printIds: number[] = [];
    const scanIds: string[] = [];
    for (const id of selected) {
      const item = items.find((i) => i.id === id);
      if (!item) continue;
      if (item.type === 'print') printIds.push(item.numericId);
      else if (item.scanId) scanIds.push(item.scanId);
    }

    bulkDeleteMutation.mutate({ printIds, scanIds });
  };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">History</h2>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Tab filter */}
        <div className="flex gap-1">
          {(['all', 'print', 'scan'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                tab === t
                  ? 'bg-ink-100 text-ink-700 dark:bg-ink-900/40 dark:text-ink-300'
                  : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
              }`}
            >
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>

        {/* Status filter */}
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          className="px-3 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300"
        >
          <option value="all">All statuses</option>
          <option value="completed">Completed</option>
          <option value="failed">Failed</option>
          <option value="held">Held</option>
          <option value="scanning">Scanning</option>
        </select>

        {/* Date filter */}
        <select
          value={dateFilter}
          onChange={(e) => setDateFilter(e.target.value as DateFilter)}
          className="px-3 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300"
        >
          <option value="all">All time</option>
          <option value="today">Today</option>
          <option value="week">This week</option>
          <option value="month">This month</option>
        </select>

        {/* Search */}
        <input
          type="text"
          placeholder="Search..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300 placeholder-gray-400 w-48"
        />
      </div>

      {/* Bulk-action bar: a rule-perf top edge sets it apart from the filter
          bar above, appearing only while a selection is active. */}
      {someSelected && (
        <div className="relative rounded-lg bg-gray-50 px-4 pb-3 pt-4 dark:bg-gray-800/40">
          <hr className="rule-perf absolute inset-x-4 top-0 text-gray-300 dark:text-gray-700" />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="font-mono text-sm text-gray-600 dark:text-gray-400">
              {selected.size} selected
            </span>
            <Button
              size="sm"
              variant="danger"
              onClick={handleBulkDelete}
              disabled={bulkDeleteMutation.isPending}
            >
              <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              {bulkDeleteMutation.isPending ? 'Deleting...' : `Delete selected (${selected.size})`}
            </Button>
          </div>
        </div>
      )}

      <Card>
        {loading ? (
          <Skeleton variant="row" count={4} />
        ) : hasError ? (
          <ErrorState onRetry={refetchAll} />
        ) : filtered.length === 0 ? (
          items.length === 0 ? (
            <EmptyState
              icon={History}
              title="Nothing here yet"
              hint="Print jobs and scans will show up here once you use Papyrus."
            />
          ) : (
            <EmptyState
              icon={SearchX}
              title="No matching items"
              hint="Try adjusting your filters or search."
            />
          )
        ) : (
          <div className="space-y-2">
            {/* Select all header */}
            <div className="flex items-center gap-3 px-3 py-2 border-b border-gray-100 dark:border-gray-800">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleAll}
                className="h-4 w-4 shrink-0"
              />
              <span className="font-mono text-xs text-gray-500 dark:text-gray-400">
                {filtered.length} item{filtered.length !== 1 ? 's' : ''}
              </span>
            </div>

            {filtered.map((item) => (
              <HistoryRow
                key={item.id}
                item={item}
                selected={selected.has(item.id)}
                onToggleSelect={toggleSelect}
                onPreview={setPreviewItem}
                onDeleteJob={deleteJobMutation.mutate}
                onDeleteScan={deleteScanMutation.mutate}
              />
            ))}
          </div>
        )}
      </Card>

      {!loading && !hasError && canLoadMore && (
        <div className="flex justify-center">
          <Button
            variant="secondary"
            onClick={() => setPageCount((c) => c + 1)}
            disabled={loadingMore}
          >
            {loadingMore ? 'Loading…' : 'Load more'}
          </Button>
        </div>
      )}

      {previewItem && (
        <FilePreviewModal
          url={previewItem.downloadUrl}
          previewUrl={previewItem.previewUrl}
          filename={previewItem.filename}
          mimeType={previewItem.mimeType}
          onClose={() => setPreviewItem(null)}
        />
      )}
    </div>
  );
}
