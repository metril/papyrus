import { useState, useEffect } from 'react';
import Button from '../common/Button';
import api from '../../api/client';
import { saveScanToCloud } from '../../api/scanner';

interface CloudProvider {
  id: number;
  provider: string;
  connected_at: string;
}

interface CloudSaveDialogProps {
  scanId: string;
  onClose: () => void;
}

const providerLabels: Record<string, string> = {
  gdrive: 'Google Drive',
  dropbox: 'Dropbox',
};

export default function CloudSaveDialog({ scanId, onClose }: CloudSaveDialogProps) {
  const [providers, setProviders] = useState<CloudProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get('/cloud/providers')
      .then(({ data }) => setProviders(data.providers))
      .catch(() => setError('Failed to load cloud providers'))
      .finally(() => setLoading(false));
  }, []);

  const handleSave = async (providerId: number) => {
    setSaving(true);
    setError(null);
    try {
      await saveScanToCloud(scanId, providerId);
      onClose();
    } catch {
      setError('Failed to upload to cloud storage.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-sm mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Save to Cloud</h3>

        {loading ? (
          <p className="text-sm text-gray-500">Loading providers...</p>
        ) : providers.length === 0 ? (
          <p className="text-sm text-gray-500">
            No cloud storage connected. Go to Settings to connect Google Drive or Dropbox.
          </p>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-gray-600 mb-3">Select a provider:</p>
            {providers.map((p) => (
              <button
                key={p.id}
                onClick={() => handleSave(p.id)}
                disabled={saving}
                className="w-full text-left p-3 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors disabled:opacity-50"
              >
                <div className="text-sm font-medium text-gray-900">
                  {providerLabels[p.provider] || p.provider}
                </div>
                <div className="text-xs text-gray-500">
                  Connected {new Date(p.connected_at).toLocaleDateString()}
                </div>
              </button>
            ))}
          </div>
        )}

        {error && (
          <p className="text-sm text-red-600 mt-3">{error}</p>
        )}

        <div className="flex justify-end pt-4">
          <Button variant="secondary" onClick={onClose}>
            {providers.length === 0 ? 'Close' : 'Cancel'}
          </Button>
        </div>
      </div>
    </div>
  );
}
