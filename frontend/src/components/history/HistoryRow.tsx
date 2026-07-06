import { memo } from 'react';
import { Download, Eye, Printer, ScanLine, Trash2 } from 'lucide-react';
import StatusBadge from '../common/StatusBadge';
import Button from '../common/Button';
import type { HistoryItem } from '../../pages/HistoryPage';

function canPreview(item: HistoryItem): boolean {
  return item.status === 'completed' || item.status === 'held';
}

export interface HistoryRowProps {
  item: HistoryItem;
  /** Primitive so toggling one row's checkbox doesn't re-render every other row. */
  selected: boolean;
  onToggleSelect: (id: string) => void;
  onPreview: (item: HistoryItem) => void;
  /** `deleteJobMutation.mutate` — already a stable reference, no wrapping needed. */
  onDeleteJob: (jobId: number) => void;
  /** `deleteScanMutation.mutate` — already a stable reference, no wrapping needed. */
  onDeleteScan: (scanId: string) => void;
}

/**
 * A single history row. Exported separately (un-memoized) as
 * `HistoryRowComponent` so tests can wrap it themselves; the default export
 * is what production code renders.
 */
export function HistoryRowComponent({ item, selected, onToggleSelect, onPreview, onDeleteJob, onDeleteScan }: HistoryRowProps) {
  const previewable = canPreview(item);
  const TypeIcon = item.type === 'print' ? Printer : ScanLine;

  // Dispatch which stable mutate-ref to call here (in the row, which already
  // knows the item's type) rather than in the parent — keeps both callback
  // props single-purpose so neither needs its own `useCallback` wrapper.
  const handleDelete = () => {
    if (item.type === 'print') onDeleteJob(item.numericId);
    else if (item.scanId) onDeleteScan(item.scanId);
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggleSelect(item.id)}
          className="h-4 w-4 shrink-0"
        />
        <TypeIcon className="h-5 w-5 shrink-0 text-gray-400 dark:text-gray-500" strokeWidth={1.75} aria-hidden="true" />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => previewable && onPreview(item)}
              className={`text-sm truncate text-left ${
                previewable
                  ? 'text-ink-600 dark:text-ink-400 hover:underline cursor-pointer'
                  : 'text-gray-900 dark:text-gray-100'
              }`}
            >
              {item.label}
            </button>
            <StatusBadge status={item.status} />
          </div>
          <div className="mt-1 font-mono text-xs text-gray-500 dark:text-gray-400">
            {item.detail} &middot; {new Date(item.time).toLocaleString()}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 sm:ml-4 sm:shrink-0">
        {previewable && (
          <Button size="sm" variant="secondary" onClick={() => onPreview(item)}>
            <Eye className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
            View
          </Button>
        )}
        {previewable && (
          <a href={item.downloadUrl} download>
            <Button size="sm" variant="secondary">
              <Download className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              Download
            </Button>
          </a>
        )}
        <Button
          size="sm"
          variant="danger"
          onClick={handleDelete}
        >
          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          Delete
        </Button>
      </div>
    </div>
  );
}

const HistoryRow = memo(HistoryRowComponent);
export default HistoryRow;
