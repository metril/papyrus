import Card from '../components/common/Card';
import UploadForm from '../components/print/UploadForm';
import JobQueue from '../components/print/JobQueue';

export default function PrintPage() {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Print</h2>

      <Card title="Upload Document">
        <UploadForm />
      </Card>

      <Card title="Print Queue">
        <JobQueue />
      </Card>
    </div>
  );
}
