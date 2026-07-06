import { TriangleAlert } from 'lucide-react';
import Button from './Button';

interface ErrorStateProps {
  title?: string;
  detail?: string;
  onRetry?: () => void;
}

// States what failed, never blames the user; offers a retry when the caller
// has one (typically a TanStack Query `refetch`).
export default function ErrorState({ title = 'Something went wrong', detail, onRetry }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-xl border border-red-100 bg-red-50 dark:border-red-900/50 dark:bg-red-950/30">
        <TriangleAlert className="h-6 w-6 text-red-500 dark:text-red-400" strokeWidth={1.75} aria-hidden="true" />
      </div>
      <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
      {detail && <p className="mt-1.5 max-w-sm text-sm text-gray-500 dark:text-gray-400">{detail}</p>}
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry} className="mt-4">
          Try again
        </Button>
      )}
    </div>
  );
}
