import { useState } from 'react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import Toggle from '../components/common/Toggle';
import ProgressBar from '../components/common/ProgressBar';
import api from '../api/client';

export default function CopyPage() {
  const [resolution, setResolution] = useState(300);
  const [mode, setMode] = useState('Color');
  const [source, setSource] = useState('Flatbed');
  const [copies, setCopies] = useState(1);
  const [duplex, setDuplex] = useState(false);
  const [media, setMedia] = useState('A4');
  const [copying, setCopying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleCopy = async () => {
    setCopying(true);
    setError(null);
    setSuccess(false);

    try {
      await api.post('/copy', { resolution, mode, source, copies, duplex, media });
      setSuccess(true);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Copy failed';
      setError(message);
    } finally {
      setCopying(false);
    }
  };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Copy</h2>

      <Card title="Quick Copy">
        <div className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Scan a document and print it immediately.
          </p>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Resolution</label>
              <select
                value={resolution}
                onChange={(e) => setResolution(Number(e.target.value))}
                disabled={copying}
                className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
              >
                {[150, 300, 600].map((r) => (
                  <option key={r} value={r}>{r} DPI</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Color</label>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value)}
                disabled={copying}
                className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
              >
                <option value="Color">Color</option>
                <option value="Gray">Grayscale</option>
                <option value="Lineart">B&W</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Source</label>
              <select
                value={source}
                onChange={(e) => setSource(e.target.value)}
                disabled={copying}
                className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
              >
                <option value="Flatbed">Flatbed</option>
                <option value="ADF">ADF</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Copies</label>
              <input
                type="number"
                min={1}
                max={99}
                value={copies}
                onChange={(e) => setCopies(Number(e.target.value))}
                disabled={copying}
                className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Paper</label>
              <select
                value={media}
                onChange={(e) => setMedia(e.target.value)}
                disabled={copying}
                className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
              >
                <option value="A4">A4</option>
                <option value="Letter">Letter</option>
              </select>
            </div>
            <div className="flex items-end">
              <Toggle checked={duplex} onChange={setDuplex} disabled={copying} label="Duplex" />
            </div>
          </div>

          {copying && <ProgressBar progress={50} label="Scanning & printing..." />}
          {error && <p className="text-sm text-red-600">{error}</p>}
          {success && (
            <p className="text-sm text-green-600 font-medium">Copy sent to printer!</p>
          )}

          <Button onClick={handleCopy} disabled={copying} className="w-full" size="lg">
            {copying ? 'Copying...' : 'Start Copy'}
          </Button>
        </div>
      </Card>
    </div>
  );
}
