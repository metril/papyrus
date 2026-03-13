import { useEffect, useRef, useCallback, useState } from 'react';
import { useScanStore } from '../../store/scanStore';
import { useWebSocket } from '../../hooks/useWebSocket';
import { getScanDownloadUrl } from '../../api/scanner';
import StatusBadge from '../common/StatusBadge';
import Button from '../common/Button';
import FilePreviewModal from '../common/FilePreviewModal';
import EmailScanDialog from './EmailScanDialog';
import CloudSaveDialog from './CloudSaveDialog';
import { useToast } from '../common/Toast';
import api from '../../api/client';
import type { ScanJob } from '../../types';

function formatSize(bytes: number | null): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function scanMimeType(scan: ScanJob): string {
  if (scan.format === 'pdf') return 'application/pdf';
  return `image/${scan.format}`;
}

export default function ScanList() {
  const { scans, loading, fetchScans, deleteScan } = useScanStore();
  const toast = useToast();
  const [emailScanId, setEmailScanId] = useState<string | null>(null);
  const [cloudScanId, setCloudScanId] = useState<string | null>(null);
  const [previewScan, setPreviewScan] = useState<ScanJob | null>(null);
  const [mergeSelection, setMergeSelection] = useState<Set<string>>(new Set());
  const [merging, setMerging] = useState(false);

  const sendToPaperless = async (scanId: string) => {
    try {
      await api.post(`/scanner/scans/${scanId}/paperless`);
      toast.show('Sent to Paperless-ngx', 'success');
    } catch {
      toast.show('Failed to send to Paperless-ngx');
    }
  };

  const applyOcr = async (scanId: string) => {
    try {
      await api.post(`/scanner/scans/${scanId}/ocr`);
      toast.show('OCR applied successfully', 'success');
      fetchScans();
    } catch {
      toast.show('Failed to apply OCR');
    }
  };

  const toggleMergeSelect = (scanId: string) => {
    setMergeSelection((prev) => {
      const next = new Set(prev);
      if (next.has(scanId)) next.delete(scanId);
      else next.add(scanId);
      return next;
    });
  };

  const handleMerge = async () => {
    if (mergeSelection.size < 2) return;
    setMerging(true);
    try {
      // Preserve order based on scan list order
      const orderedIds = scans
        .filter((s) => mergeSelection.has(s.scan_id))
        .map((s) => s.scan_id);
      await api.post('/scanner/collate', { scan_ids: orderedIds });
      toast.show('Scans merged into PDF', 'success');
      setMergeSelection(new Set());
      fetchScans();
    } catch {
      toast.show('Failed to merge scans');
    } finally {
      setMerging(false);
    }
  };

  const fetchTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const debouncedFetch = useCallback((_msg: unknown) => {
    clearTimeout(fetchTimeoutRef.current);
    fetchTimeoutRef.current = setTimeout(() => fetchScans(), 300);
  }, [fetchScans]);

  useWebSocket({
    url: '/api/system/ws/scans',
    onMessage: debouncedFetch,
  });

  useEffect(() => {
    fetchScans();
    return () => clearTimeout(fetchTimeoutRef.current);
  }, [fetchScans]);

  if (loading && scans.length === 0) {
    return <p className="text-gray-500 text-sm">Loading scans...</p>;
  }

  if (scans.length === 0) {
    return <p className="text-gray-500 text-sm">No scans yet.</p>;
  }

  const completedScans = scans.filter((s) => s.status === 'completed');

  return (
    <>
    {/* Merge toolbar */}
    {completedScans.length >= 2 && (
      <div className="flex items-center gap-3 mb-3">
        <span className="text-sm text-gray-600 dark:text-gray-400">
          {mergeSelection.size > 0
            ? `${mergeSelection.size} selected for merge`
            : 'Select scans to merge into PDF'}
        </span>
        {mergeSelection.size >= 2 && (
          <Button size="sm" onClick={handleMerge} disabled={merging}>
            {merging ? 'Merging...' : `Merge ${mergeSelection.size} to PDF`}
          </Button>
        )}
        {mergeSelection.size > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setMergeSelection(new Set())}>
            Clear
          </Button>
        )}
      </div>
    )}

    <div className="space-y-3">
      {scans.map((scan) => (
        <div
          key={scan.scan_id}
          className={`flex items-center justify-between p-4 bg-white dark:bg-gray-900 rounded-lg border ${mergeSelection.has(scan.scan_id) ? 'border-blue-400 dark:border-blue-500 ring-1 ring-blue-200 dark:ring-blue-800' : 'border-gray-200 dark:border-gray-700'}`}
        >
          {/* Merge checkbox */}
          {scan.status === 'completed' && completedScans.length >= 2 && (
            <input
              type="checkbox"
              checked={mergeSelection.has(scan.scan_id)}
              onChange={() => toggleMergeSelect(scan.scan_id)}
              className="mr-3 rounded border-gray-300 dark:border-gray-600"
            />
          )}

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <button
                onClick={() => scan.status === 'completed' && setPreviewScan(scan)}
                className={`text-sm font-medium truncate text-left ${scan.status === 'completed' ? 'text-blue-600 dark:text-blue-400 hover:underline cursor-pointer' : 'text-gray-900 dark:text-gray-100'}`}
              >
                {scan.format.toUpperCase()} &middot; {scan.resolution} DPI &middot; {scan.mode}
              </button>
              <StatusBadge status={scan.status} />
            </div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              {scan.source}
              {scan.page_count > 1 && ` \u00b7 ${scan.page_count} pages`}
              {scan.file_size && ` \u00b7 ${formatSize(scan.file_size)}`}
              {' \u00b7 '}{new Date(scan.created_at).toLocaleString()}
            </div>
          </div>

          <div className="flex gap-2 ml-4">
            {scan.status === 'completed' && (
              <>
                <Button size="sm" variant="secondary" onClick={() => setPreviewScan(scan)}>
                  View
                </Button>
                <a href={getScanDownloadUrl(scan.scan_id)} download>
                  <Button size="sm" variant="secondary">Download</Button>
                </a>
                <Button size="sm" variant="secondary" onClick={() => setEmailScanId(scan.scan_id)}>
                  Email
                </Button>
                <Button size="sm" variant="secondary" onClick={() => setCloudScanId(scan.scan_id)}>
                  Cloud
                </Button>
                {scan.format === 'pdf' && (
                  <Button size="sm" variant="secondary" onClick={() => applyOcr(scan.scan_id)}>
                    OCR
                  </Button>
                )}
                <Button size="sm" variant="secondary" onClick={() => sendToPaperless(scan.scan_id)}>
                  Paperless
                </Button>
              </>
            )}
            <Button size="sm" variant="ghost" onClick={() => deleteScan(scan.scan_id)}>
              Delete
            </Button>
          </div>
        </div>
      ))}
    </div>

    {emailScanId && (
      <EmailScanDialog scanId={emailScanId} onClose={() => setEmailScanId(null)} />
    )}
    {cloudScanId && (
      <CloudSaveDialog scanId={cloudScanId} onClose={() => setCloudScanId(null)} />
    )}
    {previewScan && (
      <FilePreviewModal
        url={getScanDownloadUrl(previewScan.scan_id)}
        filename={`scan_${previewScan.scan_id}.${previewScan.format}`}
        mimeType={scanMimeType(previewScan)}
        onClose={() => setPreviewScan(null)}
      />
    )}
    </>
  );
}
