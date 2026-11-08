import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import Button from '../common/Button';
import { scanBacks } from '../../api/scanner';
import { queryKeys } from '../../api/queries';

interface ScanBacksDialogProps {
  scanId: string;
  onClose: () => void;
}

function errorText(error: unknown): string {
  if (axios.isAxiosError<{ detail?: string }>(error)) {
    return error.response?.data?.detail ?? error.message;
  }
  return error instanceof Error ? error.message : 'Scanning the other side failed';
}

export default function ScanBacksDialog({ scanId, onClose }: ScanBacksDialogProps) {
  const queryClient = useQueryClient();
  const [rotateBacks, setRotateBacks] = useState(false);
  const [skipBlankBacks, setSkipBlankBacks] = useState(true);

  const mutation = useMutation({
    mutationFn: () =>
      scanBacks(scanId, { rotate_backs: rotateBacks, skip_blank_backs: skipBlankBacks }),
    meta: { suppressGlobalError: true },
    // Also on failure: a page-count mismatch saves the backs as a new scan.
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scans.list() });
      queryClient.invalidateQueries({ queryKey: queryKeys.scans.historyAll });
    },
    onSuccess: onClose,
  });
  const scanning = mutation.isPending;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={scanning ? undefined : onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="scan-backs-title"
        className="mx-4 w-full max-w-md rounded-xl bg-white p-6 shadow-xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="scan-backs-title" className="mb-3 text-lg font-semibold text-gray-900 dark:text-gray-100">
          Scan other side
        </h3>
        <p className="mb-4 text-sm text-gray-600 dark:text-gray-400">
          Take the stack from the output tray, flip it over as one pile, and load it back into the
          feeder. Keep the pages in order. The backs are scanned and slotted in after their fronts.
        </p>

        <div className="mb-4 space-y-3">
          <label className="flex items-start gap-3 text-sm text-gray-700 dark:text-gray-300">
            <input
              type="checkbox"
              checked={rotateBacks}
              onChange={(e) => setRotateBacks(e.target.checked)}
              disabled={scanning}
              className="mt-0.5 h-4 w-4 shrink-0"
            />
            <span>
              Flipped over the short edge (like a calendar)
              <span className="block text-xs text-gray-500 dark:text-gray-400">
                Leave off if you turned the stack like the page of a book. Turns the backs 180°.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-3 text-sm text-gray-700 dark:text-gray-300">
            <input
              type="checkbox"
              checked={skipBlankBacks}
              onChange={(e) => setSkipBlankBacks(e.target.checked)}
              disabled={scanning}
              className="mt-0.5 h-4 w-4 shrink-0"
            />
            <span>Remove blank backs</span>
          </label>
        </div>

        {scanning && (
          <p role="status" className="mb-3 text-sm text-gray-600 dark:text-gray-400">
            Scanning the backs. This can take a minute or more; keep this window open.
          </p>
        )}
        {mutation.isError && (
          <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-400">
            {errorText(mutation.error)}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={scanning}>
            {mutation.isError ? 'Close' : 'Cancel'}
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={scanning}>
            {scanning ? 'Scanning...' : 'Start'}
          </Button>
        </div>
      </div>
    </div>
  );
}
