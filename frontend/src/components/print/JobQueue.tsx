import { useCallback, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Printer } from 'lucide-react';
import { useJobs, usePrinters, queryKeys } from '../../api/queries';
import { releaseJob, cancelJob, deleteJob, reprintJob } from '../../api/printer';
import { assignJobPrinter } from '../../api/printers';
import { applyJobEvent } from '../../hooks/useRealtimeBridge';
import { getJobDownloadUrl, getJobPreviewUrl } from '../../api/scanner';
import Button from '../common/Button';
import FilePreviewModal from '../common/FilePreviewModal';
import Skeleton from '../common/Skeleton';
import EmptyState from '../common/EmptyState';
import ErrorState from '../common/ErrorState';
import JobRow from './JobRow';
import { useToast } from '../../hooks/useToast';
import type { PrintJob } from '../../types';

export default function JobQueue() {
  const queryClient = useQueryClient();
  const jobsQuery = useJobs();
  const printersQuery = usePrinters();
  const jobs = jobsQuery.data?.jobs ?? [];
  const printers = printersQuery.data ?? [];
  // Destructured because `useToast()` returns a fresh object each render while
  // `show` itself is a stable zustand action — depending on `show` keeps the
  // useCallbacks below stable without omitting deps.
  const { show } = useToast();
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

  // `mutate`/`mutateAsync` are destructured into named locals because TanStack
  // Query v5 guarantees they are stable function identities (memoized per
  // mutation observer), while the surrounding `useMutation()` result object is
  // rebuilt every render. Depending on the locals keeps the useCallbacks below
  // stable — and their JobRow props memo-friendly — with fully-listed deps.
  const { mutateAsync: releaseAsync } = useMutation({
    mutationFn: ({ id, pin }: { id: number; pin?: string }) => releaseJob(id, pin),
    meta: { suppressGlobalError: true },
    onSuccess: upsertJobIntoCache,
  });
  const { mutateAsync: cancelAsync } = useMutation({
    mutationFn: (id: number) => cancelJob(id),
    meta: { suppressGlobalError: true },
    onSuccess: upsertJobIntoCache,
  });
  const { mutateAsync: reprintAsync } = useMutation({
    mutationFn: (id: number) => reprintJob(id),
    meta: { suppressGlobalError: true },
    onSuccess: upsertJobIntoCache,
  });
  const { mutateAsync: deleteAsync } = useMutation({
    mutationFn: (id: number) => deleteJob(id),
    meta: { suppressGlobalError: true },
    onSuccess: (_result, id) =>
      applyJobEvent(queryClient, { type: 'job_deleted', data: { id } }),
  });
  // Shared across rows (mirrors the busyJobId pattern below): only one
  // printer-assign is expected in flight at a time, tracked by job id so the
  // "…" pending label only shows on the row that triggered it.
  const { mutate: assignMutate } = useMutation({
    mutationFn: ({ jobId, printerId }: { jobId: number; printerId: number }) =>
      assignJobPrinter(jobId, printerId),
    meta: { suppressGlobalError: true },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() }),
    onError: () => show('Failed to assign printer'),
    onSettled: () => setAssigningJobId(null),
  });

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
        await releaseAsync({ id: job.id });
      } catch {
        show('Failed to release job');
      } finally {
        setBusyJobId(null);
      }
    },
    [releaseAsync, show],
  );

  const handlePinSubmit = async () => {
    if (!pinJobId || pinSubmitting) return;
    setPinSubmitting(true);
    try {
      await releaseAsync({ id: pinJobId, pin: pinValue });
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
        show(`Action failed: ${msg}`);
      } finally {
        setBusyJobId(null);
      }
    },
    [show],
  );

  const handleCancel = useCallback(
    (jobId: number) => {
      handleAction(() => cancelAsync(jobId), jobId);
    },
    [handleAction, cancelAsync],
  );

  const handleReprint = useCallback(
    (jobId: number) => {
      handleAction(() => reprintAsync(jobId), jobId);
    },
    [handleAction, reprintAsync],
  );

  const handleDelete = useCallback(
    (jobId: number) => {
      handleAction(() => deleteAsync(jobId), jobId);
    },
    [handleAction, deleteAsync],
  );

  const handleAssign = useCallback(
    (jobId: number, printerId: number) => {
      setAssigningJobId(jobId);
      assignMutate({ jobId, printerId });
    },
    [assignMutate],
  );

  if (jobsQuery.isPending) {
    return <Skeleton variant="row" count={3} />;
  }

  if (jobsQuery.isError) {
    return <ErrorState onRetry={() => jobsQuery.refetch()} />;
  }

  if (jobs.length === 0) {
    return (
      <EmptyState
        icon={Printer}
        title="No jobs in the queue"
        hint="Upload a document or print to the Papyrus queue from any device"
      />
    );
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
        <div className="w-full max-w-xs rounded-xl border border-gray-200 bg-white p-6 shadow-md shadow-gray-200/50 dark:border-gray-800 dark:bg-gray-900 dark:shadow-none" onClick={(e) => e.stopPropagation()}>
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
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 p-2 text-center text-2xl tracking-widest bg-white dark:bg-gray-800 dark:text-gray-100 font-mono"
          />
          {pinError && <p className="text-sm text-red-600 dark:text-red-400 mt-2">{pinError}</p>}
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
