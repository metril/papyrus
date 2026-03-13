import { useState, useEffect } from 'react';
import Button from '../common/Button';
import ProgressBar from '../common/ProgressBar';
import { initiateScan, initiateBatchScan, getScanDownloadUrl } from '../../api/scanner';
import { useScanStore } from '../../store/scanStore';
import { useWebSocket } from '../../hooks/useWebSocket';
import api from '../../api/client';
import type { ScanJob } from '../../types';

interface ScanProfile {
  id: number;
  name: string;
  resolution: number;
  color_mode: string;
  format: string;
  source: string;
  ocr_enabled: boolean;
}

export default function ScanForm() {
  const [resolution, setResolution] = useState(300);
  const [mode, setMode] = useState('Color');
  const [format, setFormat] = useState('pdf');
  const [source, setSource] = useState('Flatbed');
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScanJob | null>(null);

  const [profiles, setProfiles] = useState<ScanProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<number | ''>('');
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileName, setProfileName] = useState('');
  const [showSaveProfile, setShowSaveProfile] = useState(false);

  const { progress, setProgress, fetchScans } = useScanStore();

  useEffect(() => {
    api.get('/scanner/profiles').then(({ data }) => setProfiles(data)).catch(() => {});
  }, []);

  const loadProfile = (profileId: number | '') => {
    setSelectedProfileId(profileId);
    if (!profileId) return;
    const p = profiles.find((pr) => pr.id === profileId);
    if (p) {
      setResolution(p.resolution);
      setMode(p.color_mode);
      setFormat(p.format);
      setSource(p.source);
    }
  };

  const handleSaveProfile = async () => {
    if (!profileName.trim()) return;
    setSavingProfile(true);
    try {
      const { data } = await api.post('/scanner/profiles', {
        name: profileName.trim(),
        resolution,
        color_mode: mode,
        format,
        source,
        ocr_enabled: false,
      });
      setProfiles((prev) => [...prev, data]);
      setSelectedProfileId(data.id);
      setShowSaveProfile(false);
      setProfileName('');
    } catch {
      // ignore
    } finally {
      setSavingProfile(false);
    }
  };

  const handleDeleteProfile = async () => {
    if (!selectedProfileId) return;
    try {
      await api.delete(`/scanner/profiles/${selectedProfileId}`);
      setProfiles((prev) => prev.filter((p) => p.id !== selectedProfileId));
      setSelectedProfileId('');
    } catch {
      // ignore
    }
  };

  useWebSocket({
    url: result?.scan_id ? `/api/scanner/ws/scan/${result.scan_id}` : null,
    onMessage: (msg) => {
      if (msg.type === 'scan_progress') {
        setProgress((msg.data as { progress: number }).progress);
      }
      if (msg.type === 'scan_completed') {
        setScanning(false);
        fetchScans();
      }
    },
  });

  const handleScan = async (batch: boolean) => {
    setScanning(true);
    setError(null);
    setResult(null);
    setProgress(0);

    try {
      const scanRequest = { resolution, mode, format, source };
      const job = batch
        ? await initiateBatchScan(scanRequest)
        : await initiateScan(scanRequest);
      setResult(job);
      await fetchScans();
    } catch (err: unknown) {
      const data = (err as { response?: { data?: unknown } }).response?.data;
      const axiosDetail = typeof data === 'string'
        ? data
        : (data as { detail?: string } | undefined)?.detail;
      const message = axiosDetail ?? (err instanceof Error ? err.message : 'Scan failed');
      setError(message);
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Profile selector */}
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Profile</label>
          <select
            value={selectedProfileId}
            onChange={(e) => loadProfile(e.target.value ? Number(e.target.value) : '')}
            disabled={scanning}
            className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="">Custom</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        {!showSaveProfile ? (
          <Button size="sm" variant="ghost" onClick={() => setShowSaveProfile(true)} disabled={scanning}>
            Save as Profile
          </Button>
        ) : (
          <div className="flex gap-1">
            <input
              type="text"
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              placeholder="Profile name"
              className="rounded-lg border-gray-300 dark:border-gray-600 text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100 w-36"
            />
            <Button size="sm" onClick={handleSaveProfile} disabled={savingProfile || !profileName.trim()}>
              Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowSaveProfile(false)}>
              Cancel
            </Button>
          </div>
        )}
        {selectedProfileId && (
          <Button size="sm" variant="ghost" onClick={handleDeleteProfile} disabled={scanning}>
            Delete
          </Button>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Resolution</label>
          <select
            value={resolution}
            onChange={(e) => setResolution(Number(e.target.value))}
            disabled={scanning}
            className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
          >
            {[75, 100, 150, 200, 300, 600].map((r) => (
              <option key={r} value={r}>{r} DPI</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Color Mode</label>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            disabled={scanning}
            className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="Color">Color</option>
            <option value="Gray">Grayscale</option>
            <option value="Lineart">Line Art</option>
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Format</label>
          <select
            value={format}
            onChange={(e) => setFormat(e.target.value)}
            disabled={scanning}
            className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="pdf">PDF</option>
            <option value="png">PNG</option>
            <option value="jpeg">JPEG</option>
            <option value="tiff">TIFF</option>
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Source</label>
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            disabled={scanning}
            className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="Flatbed">Flatbed</option>
            <option value="ADF">ADF</option>
          </select>
        </div>
      </div>

      {scanning && <ProgressBar progress={progress} label="Scanning..." />}

      {error && <p className="text-sm text-red-600">{error}</p>}

      {result && result.status === 'completed' && (
        <div className="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-lg p-4">
          <p className="text-green-800 dark:text-green-400 text-sm font-medium">Scan completed!</p>
          <a
            href={getScanDownloadUrl(result.scan_id)}
            className="text-blue-600 dark:text-blue-400 hover:underline text-sm"
            download
          >
            Download scan
          </a>
        </div>
      )}

      <div className="flex gap-3">
        <Button onClick={() => handleScan(false)} disabled={scanning} className="flex-1">
          {scanning ? 'Scanning...' : 'Scan'}
        </Button>
        <Button
          onClick={() => handleScan(true)}
          disabled={scanning}
          variant="secondary"
          className="flex-1"
        >
          {scanning ? 'Scanning...' : 'Batch Scan (ADF)'}
        </Button>
      </div>
    </div>
  );
}
