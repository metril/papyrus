import { memo, useState } from 'react';
import StatusBadge from '../common/StatusBadge';
import Button from '../common/Button';
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
  test_page: 'Test Page',
};

const sourceColors: Record<string, string> = {
  network: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  email: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400',
  cloud: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-400',
  smb: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-400',
  test_page: 'bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-400',
};

interface PrinterSelectorProps {
  job: PrintJob;
  printers: ManagedPrinter[];
  assigning: boolean;
  onAssign: (jobId: number, printerId: number) => void;
}

function PrinterSelector({ job, printers, assigning, onAssign }: PrinterSelectorProps) {
  const [selected, setSelected] = useState<number | ''>(job.printer_id ?? '');
  const physicalPrinters = printers.filter((p) => !p.is_network_queue);

  if (physicalPrinters.length <= 1) return null;

  const handleAssign = () => {
    if (!selected) return;
    onAssign(job.id, Number(selected));
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
        <Button size="sm" variant="ghost" onClick={handleAssign} disabled={assigning}>
          {assigning ? '…' : 'Move'}
        </Button>
      )}
    </div>
  );
}

export interface JobRowProps {
  job: PrintJob;
  printers: ManagedPrinter[];
  /** True while this specific job's release/cancel/reprint/delete action is in flight. */
  busy: boolean;
  /** True while this specific job's printer-assign action is in flight. */
  assigning: boolean;
  onPreview: (job: PrintJob) => void;
  onRelease: (job: PrintJob) => void;
  onCancel: (jobId: number) => void;
  onReprint: (jobId: number) => void;
  onDelete: (jobId: number) => void;
  onAssign: (jobId: number, printerId: number) => void;
}

/**
 * A single print-job row. Exported separately (un-memoized) as `JobRowComponent`
 * so tests can wrap it themselves to verify the memoization contract; the
 * default export is what production code renders.
 */
export function JobRowComponent({
  job,
  printers,
  busy,
  assigning,
  onPreview,
  onRelease,
  onCancel,
  onReprint,
  onDelete,
  onAssign,
}: JobRowProps) {
  return (
    <div className="flex items-center justify-between p-4 bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <button
            onClick={() => onPreview(job)}
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
          {job.duplex && ' · Duplex'}
          {' · '}{job.media}
          {' · '}{formatTime(job.created_at)}
        </div>
        {job.status === 'held' && printers.length > 1 && (
          <div className="mt-1.5">
            <PrinterSelector job={job} printers={printers} assigning={assigning} onAssign={onAssign} />
          </div>
        )}
        {job.error_message && (
          <p className="text-xs text-red-600 mt-1">{job.error_message}</p>
        )}
      </div>

      <div className="flex gap-2 ml-4">
        {job.status === 'held' && (
          <Button size="sm" onClick={() => onRelease(job)} disabled={busy}>
            {busy ? 'Releasing...' : 'Print'}
          </Button>
        )}
        {['held', 'printing'].includes(job.status) && (
          <Button size="sm" variant="secondary" onClick={() => onCancel(job.id)} disabled={busy}>
            Cancel
          </Button>
        )}
        {['completed', 'failed', 'cancelled'].includes(job.status) && (
          <>
            <Button size="sm" variant="secondary" onClick={() => onReprint(job.id)} disabled={busy}>
              {busy ? 'Reprinting...' : 'Reprint'}
            </Button>
            <Button size="sm" variant="danger" onClick={() => onDelete(job.id)} disabled={busy}>
              Delete
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

const JobRow = memo(JobRowComponent);
export default JobRow;
