import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useJobStore } from '../store/jobStore';
import { useScanStore } from '../store/scanStore';
import { useWebSocket } from '../hooks/useWebSocket';
import { getScanDownloadUrl, getJobDownloadUrl } from '../api/scanner';
import Card from '../components/common/Card';
import StatusBadge from '../components/common/StatusBadge';
import Button from '../components/common/Button';
import FilePreviewModal from '../components/common/FilePreviewModal';
import api from '../api/client';
import type { PrintJob, ScanJob } from '../types';

type Tab = 'all' | 'print' | 'scan';
type StatusFilter = 'all' | 'completed' | 'failed' | 'held' | 'scanning';
type DateFilter = 'all' | 'today' | 'week' | 'month';

interface HistoryItem {
  type: 'print' | 'scan';
  id: string;
  numericId: number;
  scanId?: string;
  label: string;
  status: string;
  time: string;
  detail: string;
  downloadUrl: string;
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
  const { jobs, fetchJobs, deleteJob } = useJobStore();
  const { scans, fetchScans, deleteScan } = useScanStore();

  const [tab, setTab] = useState<Tab>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [dateFilter, setDateFilter] = useState<DateFilter>('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [previewItem, setPreviewItem] = useState<HistoryItem | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [loading, setLoading] = useState(true);

  // Debounced WS refetch
  const fetchTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const debouncedFetch = useCallback(() => {
    clearTimeout(fetchTimeoutRef.current);
    fetchTimeoutRef.current = setTimeout(() => {
      fetchJobs();
      fetchScans();
    }, 300);
  }, [fetchJobs, fetchScans]);

  useWebSocket({ url: '/api/system/ws/jobs', onMessage: debouncedFetch });
  useWebSocket({ url: '/api/system/ws/scans', onMessage: debouncedFetch });

  useEffect(() => {
    Promise.all([fetchJobs(), fetchScans()]).finally(() => setLoading(false));
    return () => clearTimeout(fetchTimeoutRef.current);
  }, [fetchJobs, fetchScans]);

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

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filtered.map((i) => i.id)));
    }
  };

  const handleBulkDelete = async () => {
    if (!someSelected) return;
    setBulkDeleting(true);

    const printIds: number[] = [];
    const scanIds: string[] = [];
    for (const id of selected) {
      const item = items.find((i) => i.id === id);
      if (!item) continue;
      if (item.type === 'print') printIds.push(item.numericId);
      else if (item.scanId) scanIds.push(item.scanId);
    }

    try {
      const promises: Promise<unknown>[] = [];
      if (printIds.length > 0) {
        promises.push(api.post('/jobs/bulk-delete', { ids: printIds }));
      }
      if (scanIds.length > 0) {
        promises.push(api.post('/scanner/scans/bulk-delete', { scan_ids: scanIds }));
      }
      await Promise.all(promises);
      await Promise.all([fetchJobs(), fetchScans()]);
      setSelected(new Set());
    } catch {
      // Errors handled by interceptor
    } finally {
      setBulkDeleting(false);
    }
  };

  const handleDeleteSingle = async (item: HistoryItem) => {
    if (item.type === 'print') {
      await deleteJob(item.numericId);
    } else if (item.scanId) {
      await deleteScan(item.scanId);
    }
  };

  const canPreview = (item: HistoryItem) =>
    item.status === 'completed' || item.status === 'held';

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
                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
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

        {/* Bulk actions */}
        {someSelected && (
          <Button
            size="sm"
            variant="danger"
            onClick={handleBulkDelete}
            disabled={bulkDeleting}
          >
            {bulkDeleting ? 'Deleting...' : `Delete Selected (${selected.size})`}
          </Button>
        )}
      </div>

      <Card>
        {loading ? (
          <p className="text-gray-500 text-sm">Loading history...</p>
        ) : filtered.length === 0 ? (
          <p className="text-gray-500 text-sm">No items match your filters.</p>
        ) : (
          <div className="space-y-2">
            {/* Select all header */}
            <div className="flex items-center gap-3 px-3 py-2 border-b border-gray-100 dark:border-gray-800">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleAll}
                className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              />
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {filtered.length} item{filtered.length !== 1 ? 's' : ''}
              </span>
            </div>

            {filtered.map((item) => (
              <div
                key={item.id}
                className="flex items-center gap-3 p-3 rounded-lg border border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50"
              >
                <input
                  type="checkbox"
                  checked={selected.has(item.id)}
                  onChange={() => toggleSelect(item.id)}
                  className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 flex-shrink-0"
                />

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold uppercase text-gray-400 dark:text-gray-500">
                      {item.type}
                    </span>
                    <button
                      onClick={() => canPreview(item) && setPreviewItem(item)}
                      className={`text-sm truncate text-left ${
                        canPreview(item)
                          ? 'text-blue-600 dark:text-blue-400 hover:underline cursor-pointer'
                          : 'text-gray-900 dark:text-gray-100'
                      }`}
                    >
                      {item.label}
                    </button>
                    <StatusBadge status={item.status} />
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    {item.detail} &middot; {new Date(item.time).toLocaleString()}
                  </div>
                </div>

                <div className="flex gap-2 ml-4 flex-shrink-0">
                  {canPreview(item) && (
                    <Button size="sm" variant="secondary" onClick={() => setPreviewItem(item)}>
                      View
                    </Button>
                  )}
                  {canPreview(item) && (
                    <a href={item.downloadUrl} download>
                      <Button size="sm" variant="secondary">Download</Button>
                    </a>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleDeleteSingle(item)}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {previewItem && (
        <FilePreviewModal
          url={previewItem.downloadUrl}
          filename={previewItem.filename}
          mimeType={previewItem.mimeType}
          onClose={() => setPreviewItem(null)}
        />
      )}
    </div>
  );
}
