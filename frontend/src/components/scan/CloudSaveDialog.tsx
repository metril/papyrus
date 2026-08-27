import { useState } from 'react';
import Button from '../common/Button';
import { saveScanToCloud } from '../../api/scanner';
import { uploadScanToWebdav } from '../../api/cloud';
import { useCloudProviders } from '../../api/queries';
import { getProviderLabel } from '../../lib/providerLabels';
import type { CloudProvider } from '../../types';

interface CloudSaveDialogProps {
  scanId: string;
  onClose: () => void;
}

// F149: routes a save to the right backend API for the provider's kind —
// gdrive/dropbox/onedrive share one endpoint, webdav has its own (no generic
// /cloud/* dispatch handles "webdav"). The `never` default makes adding a
// provider without updating this switch a compile error.
async function saveToProvider(provider: CloudProvider, scanId: string): Promise<unknown> {
  switch (provider.provider) {
    case 'gdrive':
    case 'dropbox':
    case 'onedrive':
      return saveScanToCloud(scanId, provider.id);
    case 'webdav':
      return uploadScanToWebdav(provider.id, scanId);
    default: {
      const unreachable: never = provider.provider;
      throw new Error(`Unhandled cloud provider: ${unreachable}`);
    }
  }
}

export default function CloudSaveDialog({ scanId, onClose }: CloudSaveDialogProps) {
  // F149: was a raw `api.get('/cloud/providers')` in its own effect — bypassed
  // the Query cache (a fresh network round-trip every time the dialog opened)
  // and duplicated the provider-label map, which only covered gdrive/dropbox
  // and rendered anything else (onedrive, webdav) as its literal provider key.
  const { data: providers = [], isPending: loading } = useCloudProviders();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async (provider: CloudProvider) => {
    setSaving(true);
    setError(null);
    try {
      await saveToProvider(provider, scanId);
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
        className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-sm mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">Save to Cloud</h3>

        {loading ? (
          <p className="text-sm text-gray-500">Loading providers...</p>
        ) : providers.length === 0 ? (
          <p className="text-sm text-gray-500">
            No cloud storage connected. Go to Settings to connect a provider.
          </p>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">Select a provider:</p>
            {providers.map((p) => (
              <button
                key={p.id}
                onClick={() => handleSave(p)}
                disabled={saving}
                className="w-full text-left p-3 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors disabled:opacity-50"
              >
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  {getProviderLabel(p.provider)}
                </div>
                <div className="text-xs text-gray-500 dark:text-gray-400">
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
