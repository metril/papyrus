import { useEffect, useRef, useCallback, useState } from 'react';
import { useJobStore } from '../../store/jobStore';
import { useWebSocket } from '../../hooks/useWebSocket';
import { getJobDownloadUrl } from '../../api/scanner';
import { listPrinters, assignJobPrinter } from '../../api/printers';
import StatusBadge from '../common/StatusBadge';
import Button from '../common/Button';
import FilePreviewModal from '../common/FilePreviewModal';
import type { PrintJob, ManagedPrinter } from '../../types';

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(dateStr: string): string {
  return new Date(dateStr).toLocaleString();
}

const sourceLabels: Record<string, string> = {
  upload: 'Upload',
  smb: 'SMB',
  cloud: 'Cloud',
  email: 'Email',
  network: 'Network',
};

const sourceColors: Record<string, string> = {
  network: 'bg-blue-100 text-blue-700',
  email: 'bg-purple-100 text-purple-700',
  cloud: 'bg-cyan-100 text-cyan-700',
  smb: 'bg-orange-100 text-orange-700',
};

function PrinterSelector({ job, printers, onAssigned }: { job: PrintJob; printers: ManagedPrinter[]; onAssigned: () => void }) {
  const [selected, setSelected] = useState<number | ''>(job.printer_id ?? '');
  const [saving, setSaving] = useState(false);

  const physicalPrinters = printers.filter((p) => !p.is_network_queue);

  if (physicalPrinters.length <= 1) return null;

  const handleAssign = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await assignJobPrinter(job.id, Number(selected));
      onAssigned();
    } catch { /* ignore */ }
    finally { setSaving(false); }
  };

  return (
    <div className="flex items-center gap-1">
      <select
        value={selected}
        onChange={(e) => setSelected(e.target.value ? Number(e.target.value) : '')}
        className="text-xs rounded border border-gray-300 py-0.5 px-1"
      >
        <option value="">— printer —</option>
        {physicalPrinters.map((p) => (
          <option key={p.id} value={p.id}>{p.display_name}</option>
        ))}
      </select>
      {selected !== (job.printer_id ?? '') && (
        <Button size="sm" variant="ghost" onClick={handleAssign} disabled={saving}>
          {saving ? '…' : 'Move'}
        </Button>
      )}
    </div>
  );
}

export default function JobQueue() {
  const { jobs, loading, fetchJobs, releaseJob, cancelJob, deleteJob } = useJobStore();
  const [previewJob, setPreviewJob] = useState<PrintJob | null>(null);
  const [printers, setPrinters] = useState<ManagedPrinter[]>([]);

  const fetchTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const debouncedFetch = useCallback((_msg: unknown) => {
    clearTimeout(fetchTimeoutRef.current);
    fetchTimeoutRef.current = setTimeout(() => fetchJobs(), 300);
  }, [fetchJobs]);

  useWebSocket({
    url: '/api/system/ws/jobs',
    onMessage: debouncedFetch,
  });

  useEffect(() => {
    fetchJobs();
    listPrinters().then(setPrinters).catch(() => {});
    return () => clearTimeout(fetchTimeoutRef.current);
  }, [fetchJobs]);

  if (loading && jobs.length === 0) {
    return <p className="text-gray-500 text-sm">Loading jobs...</p>;
  }

  if (jobs.length === 0) {
    return <p className="text-gray-500 text-sm">No print jobs yet. Upload a file to get started.</p>;
  }

  return (
    <>
    <div className="space-y-3">
      {jobs.map((job) => (
        <div
          key={job.id}
          className="flex items-center justify-between p-4 bg-white rounded-lg border border-gray-200"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPreviewJob(job)}
                className="text-sm font-medium text-blue-600 hover:underline truncate text-left"
              >
                {job.filename}
              </button>
              <StatusBadge status={job.status} />
              {job.source_type && job.source_type !== 'upload' && (
                <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${sourceColors[job.source_type] || 'bg-gray-100 text-gray-600'}`}>
                  {sourceLabels[job.source_type] || job.source_type}
                </span>
              )}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {formatSize(job.file_size)} &middot; {job.copies} cop{job.copies > 1 ? 'ies' : 'y'}
              {job.duplex && ' \u00b7 Duplex'}
              {' \u00b7 '}{job.media}
              {' \u00b7 '}{formatTime(job.created_at)}
            </div>
            {job.status === 'held' && printers.length > 1 && (
              <div className="mt-1.5">
                <PrinterSelector job={job} printers={printers} onAssigned={fetchJobs} />
              </div>
            )}
            {job.error_message && (
              <p className="text-xs text-red-600 mt-1">{job.error_message}</p>
            )}
          </div>

          <div className="flex gap-2 ml-4">
            {job.status === 'held' && (
              <Button size="sm" onClick={() => releaseJob(job.id)}>
                Print
              </Button>
            )}
            {['held', 'printing'].includes(job.status) && (
              <Button size="sm" variant="secondary" onClick={() => cancelJob(job.id)}>
                Cancel
              </Button>
            )}
            {['completed', 'failed', 'cancelled'].includes(job.status) && (
              <Button size="sm" variant="ghost" onClick={() => deleteJob(job.id)}>
                Delete
              </Button>
            )}
          </div>
        </div>
      ))}
    </div>

    {previewJob && (
      <FilePreviewModal
        url={getJobDownloadUrl(previewJob.id)}
        filename={previewJob.filename}
        mimeType={previewJob.mime_type}
        onClose={() => setPreviewJob(null)}
      />
    )}
    </>
  );
}
