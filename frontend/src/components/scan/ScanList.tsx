import { useEffect } from 'react';
import { useScanStore } from '../../store/scanStore';
import StatusBadge from '../common/StatusBadge';
import Button from '../common/Button';
import { getScanDownloadUrl } from '../../api/scanner';

function formatSize(bytes: number | null): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ScanList() {
  const { scans, loading, fetchScans, deleteScan } = useScanStore();

  useEffect(() => {
    fetchScans();
  }, [fetchScans]);

  if (loading && scans.length === 0) {
    return <p className="text-gray-500 text-sm">Loading scans...</p>;
  }

  if (scans.length === 0) {
    return <p className="text-gray-500 text-sm">No scans yet.</p>;
  }

  return (
    <div className="space-y-3">
      {scans.map((scan) => (
        <div
          key={scan.scan_id}
          className="flex items-center justify-between p-4 bg-white rounded-lg border border-gray-200"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-900">
                {scan.format.toUpperCase()} &middot; {scan.resolution} DPI &middot; {scan.mode}
              </span>
              <StatusBadge status={scan.status} />
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {scan.source}
              {scan.page_count > 1 && ` \u00b7 ${scan.page_count} pages`}
              {scan.file_size && ` \u00b7 ${formatSize(scan.file_size)}`}
              {' \u00b7 '}{new Date(scan.created_at).toLocaleString()}
            </div>
          </div>

          <div className="flex gap-2 ml-4">
            {scan.status === 'completed' && (
              <a href={getScanDownloadUrl(scan.scan_id)} download>
                <Button size="sm" variant="secondary">Download</Button>
              </a>
            )}
            <Button size="sm" variant="ghost" onClick={() => deleteScan(scan.scan_id)}>
              Delete
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
