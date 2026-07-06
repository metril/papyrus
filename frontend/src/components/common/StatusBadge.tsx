interface StatusBadgeProps {
  status: string;
}

type StatusFamily = 'success' | 'active' | 'pending' | 'error' | 'neutral';

// Every status string the app currently passes (held/converting/printing/
// scanning/completed/failed/cancelled/deleted — see JobRow/ScanRow/HistoryRow)
// plus the wider vocabulary future pages may use for the same families.
const statusFamily: Record<string, StatusFamily> = {
  ready: 'success',
  completed: 'success',
  success: 'success',
  printing: 'active',
  processing: 'active',
  converting: 'active',
  scanning: 'active',
  held: 'pending',
  pending: 'pending',
  error: 'error',
  failed: 'error',
  offline: 'neutral',
  unknown: 'neutral',
  cancelled: 'neutral',
  deleted: 'neutral',
};

const dotClasses: Record<StatusFamily, string> = {
  success: 'text-green-500 dark:text-green-400',
  active: 'text-ink-500 dark:text-ink-400',
  pending: 'text-amber-500 dark:text-amber-400',
  error: 'text-red-500 dark:text-red-400',
  neutral: 'text-gray-400',
};

function sentenceCase(value: string): string {
  const spaced = value.replace(/[_-]/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const family = statusFamily[status] ?? 'neutral';
  const pulse = family === 'active';
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-gray-100 bg-gray-50 px-2.5 py-1 text-xs font-medium text-gray-700 dark:border-gray-800 dark:bg-gray-800/60 dark:text-gray-300">
      <span aria-hidden="true" className={`led ${dotClasses[family]} ${pulse ? 'led-pulse' : ''}`} />
      {sentenceCase(status)}
    </span>
  );
}
