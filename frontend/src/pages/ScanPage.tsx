import Card from '../components/common/Card';
import ScanForm from '../components/scan/ScanForm';
import ScanList from '../components/scan/ScanList';

export default function ScanPage() {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Scan</h2>

      <Card title="New Scan">
        <ScanForm />
      </Card>

      <Card title="Recent Scans">
        <ScanList />
      </Card>
    </div>
  );
}
