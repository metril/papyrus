import { memo } from 'react';
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

  // Dispatch which stable mutate-ref to call here (in the row, which already
  // knows the item's type) rather than in the parent — keeps both callback
  // props single-purpose so neither needs its own `useCallback` wrapper.
  const handleDelete = () => {
    if (item.type === 'print') onDeleteJob(item.numericId);
    else if (item.scanId) onDeleteScan(item.scanId);
  };

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50">
      <input
        type="checkbox"
        checked={selected}
        onChange={() => onToggleSelect(item.id)}
        className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 flex-shrink-0"
      />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase text-gray-400 dark:text-gray-500">
            {item.type}
          </span>
          <button
            onClick={() => previewable && onPreview(item)}
            className={`text-sm truncate text-left ${
              previewable
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
        {previewable && (
          <Button size="sm" variant="secondary" onClick={() => onPreview(item)}>
            View
          </Button>
        )}
        {previewable && (
          <a href={item.downloadUrl} download>
            <Button size="sm" variant="secondary">Download</Button>
          </a>
        )}
        <Button
          size="sm"
          variant="danger"
          onClick={handleDelete}
        >
          Delete
        </Button>
      </div>
    </div>
  );
}

const HistoryRow = memo(HistoryRowComponent);
export default HistoryRow;
