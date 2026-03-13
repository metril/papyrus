import { useEffect, useRef, useCallback, useState } from 'react';
import { useJobStore } from '../../store/jobStore';
import { useWebSocket } from '../../hooks/useWebSocket';
import { getJobDownloadUrl } from '../../api/scanner';
import { listPrinters, assignJobPrinter } from '../../api/printers';
import StatusBadge from '../common/StatusBadge';
import Button from '../common/Button';
import FilePreviewModal from '../common/FilePreviewModal';
import { useToast } from '../common/Toast';
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
  network: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  email: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400',
  cloud: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-400',
  smb: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-400',
};

function PrinterSelector({ job, printers, onAssigned }: { job: PrintJob; printers: ManagedPrinter[]; onAssigned: () => void }) {
  const [selected, setSelected] = useState<number | ''>(job.printer_id ?? '');
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const physicalPrinters = printers.filter((p) => !p.is_network_queue);

  if (physicalPrinters.length <= 1) return null;

  const handleAssign = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await assignJobPrinter(job.id, Number(selected));
      onAssigned();
    } catch {
      toast.show('Failed to assign printer');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex items-center gap-1">
      <select
        value={selected}
        onChange={(e) => setSelected(e.target.value ? Number(e.target.value) : '')}
        aria-label="Select printer"
        className="text-xs rounded border border-gray-300 dark:border-gray-600 py-0.5 px-1 bg-white dark:bg-gray-800 dark:text-gray-100"
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
  const { jobs, loading, fetchJobs, releaseJob, cancelJob, deleteJob, reprintJob } = useJobStore();
  const toast = useToast();
  const [previewJob, setPreviewJob] = useState<PrintJob | null>(null);
  const [printers, setPrinters] = useState<ManagedPrinter[]>([]);
  const [pinJobId, setPinJobId] = useState<number | null>(null);
  const [pinValue, setPinValue] = useState('');
  const [pinError, setPinError] = useState('');
  const [pinSubmitting, setPinSubmitting] = useState(false);
  const [busyJobId, setBusyJobId] = useState<number | null>(null);
  const pinInputRef = useRef<HTMLInputElement>(null);

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

  const handleRelease = async (job: PrintJob) => {
    if (job.has_pin) {
      setPinJobId(job.id);
      setPinValue('');
      setPinError('');
      return;
    }
    setBusyJobId(job.id);
    try {
      await releaseJob(job.id);
    } catch {
      toast.show('Failed to release job');
    } finally {
      setBusyJobId(null);
    }
  };

  const handlePinSubmit = async () => {
    if (!pinJobId || pinSubmitting) return;
    setPinSubmitting(true);
    try {
      await releaseJob(pinJobId, pinValue);
      setPinJobId(null);
    } catch {
      setPinError('Invalid PIN');
      pinInputRef.current?.focus();
    } finally {
      setPinSubmitting(false);
    }
  };

  const handleAction = async (action: () => Promise<void>, jobId: number) => {
    setBusyJobId(jobId);
    try {
      await action();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      toast.show(`Action failed: ${msg}`);
    } finally {
      setBusyJobId(null);
    }
  };

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
          className="flex items-center justify-between p-4 bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPreviewJob(job)}
                className="text-sm font-medium text-blue-600 dark:text-blue-400 hover:underline truncate text-left"
              >
                {job.filename}
              </button>
              <StatusBadge status={job.status} />
              {job.source_type && job.source_type !== 'upload' && (
                <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${sourceColors[job.source_type] || 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>
                  {sourceLabels[job.source_type] || job.source_type}
                </span>
              )}
              {job.has_pin && job.status === 'held' && (
                <span className="text-xs px-1.5 py-0.5 rounded-full font-medium bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                  PIN
                </span>
              )}
            </div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
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
              <Button size="sm" onClick={() => handleRelease(job)} disabled={busyJobId === job.id}>
                {busyJobId === job.id ? 'Releasing...' : 'Print'}
              </Button>
            )}
            {['held', 'printing'].includes(job.status) && (
              <Button size="sm" variant="secondary" onClick={() => handleAction(() => cancelJob(job.id), job.id)} disabled={busyJobId === job.id}>
                Cancel
              </Button>
            )}
            {['completed', 'failed', 'cancelled'].includes(job.status) && (
              <>
                <Button size="sm" variant="secondary" onClick={() => handleAction(() => reprintJob(job.id), job.id)} disabled={busyJobId === job.id}>
                  {busyJobId === job.id ? 'Reprinting...' : 'Reprint'}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => handleAction(() => deleteJob(job.id), job.id)} disabled={busyJobId === job.id}>
                  Delete
                </Button>
              </>
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

    {/* PIN entry dialog */}
    {pinJobId !== null && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setPinJobId(null)} role="dialog" aria-label="Enter release PIN">
        <div className="bg-white dark:bg-gray-900 rounded-xl p-6 w-full max-w-xs shadow-xl" onClick={(e) => e.stopPropagation()}>
          <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">Enter Release PIN</h3>
          <input
            ref={pinInputRef}
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            maxLength={10}
            value={pinValue}
            onChange={(e) => { setPinValue(e.target.value); setPinError(''); }}
            onKeyDown={(e) => e.key === 'Enter' && handlePinSubmit()}
            placeholder="PIN"
            autoFocus
            aria-label="Release PIN"
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 p-2 text-center text-2xl tracking-widest bg-white dark:bg-gray-800 dark:text-gray-100"
          />
          {pinError && <p className="text-sm text-red-600 mt-2">{pinError}</p>}
          <div className="flex gap-2 justify-end mt-4">
            <Button size="sm" variant="secondary" onClick={() => setPinJobId(null)}>Cancel</Button>
            <Button size="sm" onClick={handlePinSubmit} disabled={pinSubmitting || !pinValue}>
              {pinSubmitting ? 'Releasing...' : 'Release'}
            </Button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}
