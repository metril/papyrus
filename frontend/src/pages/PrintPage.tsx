import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import Card from '../components/common/Card';
import PrinterStatus from '../components/common/PrinterStatus';
import UploadForm from '../components/print/UploadForm';
import JobQueue from '../components/print/JobQueue';
import { useToast } from '../hooks/useToast';

export default function PrintPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { show } = useToast();

  // F133: the PWA share-target route redirects here with `?share_failed=<n>`
  // when one or more shared files couldn't be added to the queue (bad type,
  // oversize, ...) rather than aborting the whole share. Surface it once as
  // a toast, then drop the param so a page refresh doesn't re-show it.
  useEffect(() => {
    const failedParam = searchParams.get('share_failed');
    if (!failedParam) return;

    const count = Number(failedParam);
    show(
      count === 1
        ? '1 shared file could not be added to the print queue.'
        : `${count} shared files could not be added to the print queue.`,
    );
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('share_failed');
        return next;
      },
      { replace: true },
    );
  }, [searchParams, show, setSearchParams]);

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Print</h2>

      <PrinterStatus />

      <Card title="Upload Document">
        <UploadForm />
      </Card>

      <Card title="Print Queue">
        <JobQueue />
      </Card>
    </div>
  );
}
