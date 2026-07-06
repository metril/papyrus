import { useCallback, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useJobs, usePrinters, queryKeys } from '../../api/queries';
import { releaseJob, cancelJob, deleteJob, reprintJob } from '../../api/printer';
import { assignJobPrinter } from '../../api/printers';
import { applyJobEvent } from '../../hooks/useRealtimeBridge';
import { getJobDownloadUrl, getJobPreviewUrl } from '../../api/scanner';
import Button from '../common/Button';
import FilePreviewModal from '../common/FilePreviewModal';
import JobRow from './JobRow';
import { useToast } from '../../hooks/useToast';
import type { PrintJob } from '../../types';

export default function JobQueue() {
  const queryClient = useQueryClient();
  const jobsQuery = useJobs();
  const printersQuery = usePrinters();
  const jobs = jobsQuery.data?.jobs ?? [];
  const printers = printersQuery.data ?? [];
  const toast = useToast();
  const [previewJob, setPreviewJob] = useState<PrintJob | null>(null);
  const [pinJobId, setPinJobId] = useState<number | null>(null);
  const [pinValue, setPinValue] = useState('');
  const [pinError, setPinError] = useState('');
  const [pinSubmitting, setPinSubmitting] = useState(false);
  const [busyJobId, setBusyJobId] = useState<number | null>(null);
  const [assigningJobId, setAssigningJobId] = useState<number | null>(null);
  const pinInputRef = useRef<HTMLInputElement>(null);

  // Job actions return the updated job. Upsert it into the cache via the same
  // guarded updater the WS bridge uses, so the broadcast that follows is an
  // idempotent same-id replace (no total drift). NO blanket invalidation.
  const upsertJobIntoCache = useCallback(
    (job: PrintJob) =>
      applyJobEvent(queryClient, {
        type: 'job_updated',
        data: job as unknown as Record<string, unknown>,
      }),
    [queryClient],
  );

  const releaseMutation = useMutation({
    mutationFn: ({ id, pin }: { id: number; pin?: string }) => releaseJob(id, pin),
    meta: { suppressGlobalError: true },
    onSuccess: upsertJobIntoCache,
  });
  const cancelMutation = useMutation({
    mutationFn: (id: number) => cancelJob(id),
    meta: { suppressGlobalError: true },
    onSuccess: upsertJobIntoCache,
  });
  const reprintMutation = useMutation({
    mutationFn: (id: number) => reprintJob(id),
    meta: { suppressGlobalError: true },
    onSuccess: upsertJobIntoCache,
  });
  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteJob(id),
    meta: { suppressGlobalError: true },
    onSuccess: (_result, id) =>
      applyJobEvent(queryClient, { type: 'job_deleted', data: { id } }),
  });
  // Shared across rows (mirrors the busyJobId pattern below): only one
  // printer-assign is expected in flight at a time, tracked by job id so the
  // "…" pending label only shows on the row that triggered it.
  const assignMutation = useMutation({
    mutationFn: ({ jobId, printerId }: { jobId: number; printerId: number }) =>
      assignJobPrinter(jobId, printerId),
    meta: { suppressGlobalError: true },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() }),
    onError: () => toast.show('Failed to assign printer'),
    onSettled: () => setAssigningJobId(null),
  });

  // The callbacks below intentionally depend on `mutation.mutateAsync` /
  // `toast.show` rather than the whole `releaseMutation` / `toast` objects:
  // `useMutation()` and `useToast()` both return a fresh object every render,
  // so depending on the object itself would recreate the callback (and thus
  // the JobRow prop) every render, defeating this row's memoization. The
  // narrower properties ARE stable (React Query memoizes `.mutate(Async)` per
  // mutation observer; `toast.show` is a stable zustand action). This trips
  // eslint-plugin-react-hooks' exhaustive-deps / compiler-preservation checks,
  // which don't recognize that narrower stability guarantee — expected,
  // low-severity warnings, not a bug.
  const handleRelease = useCallback(
    async (job: PrintJob) => {
      if (job.has_pin) {
        setPinJobId(job.id);
        setPinValue('');
        setPinError('');
        return;
      }
      setBusyJobId(job.id);
      try {
        await releaseMutation.mutateAsync({ id: job.id });
      } catch {
        toast.show('Failed to release job');
      } finally {
        setBusyJobId(null);
      }
    },
    [releaseMutation.mutateAsync, toast.show],
  );

  const handlePinSubmit = async () => {
    if (!pinJobId || pinSubmitting) return;
    setPinSubmitting(true);
    try {
      await releaseMutation.mutateAsync({ id: pinJobId, pin: pinValue });
      setPinJobId(null);
    } catch {
      setPinError('Invalid PIN');
      pinInputRef.current?.focus();
    } finally {
      setPinSubmitting(false);
    }
  };

  const handleAction = useCallback(
    async (action: () => Promise<unknown>, jobId: number) => {
      setBusyJobId(jobId);
      try {
        await action();
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : 'Unknown error';
        toast.show(`Action failed: ${msg}`);
      } finally {
        setBusyJobId(null);
      }
    },
    [toast.show],
  );

  const handleCancel = useCallback(
    (jobId: number) => {
      handleAction(() => cancelMutation.mutateAsync(jobId), jobId);
    },
    [handleAction, cancelMutation.mutateAsync],
  );

  const handleReprint = useCallback(
    (jobId: number) => {
      handleAction(() => reprintMutation.mutateAsync(jobId), jobId);
    },
    [handleAction, reprintMutation.mutateAsync],
  );

  const handleDelete = useCallback(
    (jobId: number) => {
      handleAction(() => deleteMutation.mutateAsync(jobId), jobId);
    },
    [handleAction, deleteMutation.mutateAsync],
  );

  const handleAssign = useCallback(
    (jobId: number, printerId: number) => {
      setAssigningJobId(jobId);
      assignMutation.mutate({ jobId, printerId });
    },
    [assignMutation.mutate],
  );

  if (jobsQuery.isLoading && jobs.length === 0) {
    return <p className="text-gray-500 text-sm">Loading jobs...</p>;
  }

  if (jobs.length === 0) {
    return <p className="text-gray-500 text-sm">No print jobs yet. Upload a file to get started.</p>;
  }

  return (
    <>
    <div className="space-y-3">
      {jobs.map((job) => (
        <JobRow
          key={job.id}
          job={job}
          printers={printers}
          busy={busyJobId === job.id}
          assigning={assigningJobId === job.id}
          onPreview={setPreviewJob}
          onRelease={handleRelease}
          onCancel={handleCancel}
          onReprint={handleReprint}
          onDelete={handleDelete}
          onAssign={handleAssign}
        />
      ))}
    </div>

    {previewJob && (
      <FilePreviewModal
        url={getJobDownloadUrl(previewJob.id)}
        previewUrl={getJobPreviewUrl(previewJob.id)}
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
