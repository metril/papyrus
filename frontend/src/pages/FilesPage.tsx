import { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import api from '../api/client';
import { listProviders, listFiles, getDownloadUrl } from '../api/cloud';
import type { SMBShare, SMBFileEntry, CloudProvider, CloudFileEntry } from '../types';

type Tab = 'network' | 'cloud';

export default function FilesPage() {
  const [activeTab, setActiveTab] = useState<Tab>('network');

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <h2 className="text-2xl font-bold text-gray-900">Files</h2>
        <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
          <button
            onClick={() => setActiveTab('network')}
            className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
              activeTab === 'network'
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Network
          </button>
          <button
            onClick={() => setActiveTab('cloud')}
            className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
              activeTab === 'cloud'
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Cloud
          </button>
        </div>
      </div>

      {activeTab === 'network' ? <NetworkBrowser /> : <CloudBrowser />}
    </div>
  );
}

// --- Network (SMB) Browser ---

function NetworkBrowser() {
  const [shares, setShares] = useState<SMBShare[]>([]);
  const [selectedShare, setSelectedShare] = useState<SMBShare | null>(null);
  const [currentPath, setCurrentPath] = useState('/');
  const [files, setFiles] = useState<SMBFileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get('/smb/shares').then(({ data }) => setShares(data));
  }, []);

  const browse = async (share: SMBShare, path: string) => {
    setLoading(true);
    setError(null);
    setSelectedShare(share);
    setCurrentPath(path);

    try {
      const { data } = await api.get(`/smb/browse/${share.id}`, { params: { path } });
      setFiles(data);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to browse share';
      setError(message);
      setFiles([]);
    } finally {
      setLoading(false);
    }
  };

  const navigateTo = (entry: SMBFileEntry) => {
    if (!selectedShare) return;
    if (entry.is_directory) {
      const newPath = currentPath === '/' ? `/${entry.name}` : `${currentPath}/${entry.name}`;
      browse(selectedShare, newPath);
    }
  };

  const goUp = () => {
    if (!selectedShare || currentPath === '/') return;
    const parent = currentPath.split('/').slice(0, -1).join('/') || '/';
    browse(selectedShare, parent);
  };

  const printFile = async (entry: SMBFileEntry) => {
    if (!selectedShare) return;
    const filePath = currentPath === '/' ? `/${entry.name}` : `${currentPath}/${entry.name}`;
    try {
      const response = await api.get(`/smb/download/${selectedShare.id}`, {
        params: { path: filePath },
        responseType: 'blob',
      });
      const file = new File([response.data], entry.name);
      const formData = new FormData();
      formData.append('file', file);
      formData.append('copies', '1');
      formData.append('duplex', 'false');
      formData.append('media', 'A4');
      formData.append('hold', 'true');
      await api.post('/jobs/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      alert('File added to print queue (held)');
    } catch {
      alert('Failed to print file');
    }
  };

  if (!selectedShare) {
    return (
      <Card title="SMB Shares">
        {shares.length === 0 ? (
          <p className="text-gray-500 text-sm">
            No shares configured. Add them in Settings.
          </p>
        ) : (
          <div className="space-y-2">
            {shares.map((share) => (
              <button
                key={share.id}
                onClick={() => browse(share, '/')}
                className="w-full flex items-center gap-3 p-3 rounded-lg hover:bg-gray-50 border border-gray-200 text-left"
              >
                <span className="text-lg">&#128193;</span>
                <div>
                  <div className="font-medium text-gray-900">{share.name}</div>
                  <div className="text-xs text-gray-500">
                    \\{share.server}\{share.share_name}
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </Card>
    );
  }

  return (
    <Card title={`${selectedShare.name} - ${currentPath}`}>
      <div className="space-y-2">
        <div className="flex gap-2 mb-4">
          <Button size="sm" variant="secondary" onClick={() => setSelectedShare(null)}>
            All Shares
          </Button>
          {currentPath !== '/' && (
            <Button size="sm" variant="ghost" onClick={goUp}>
              Up
            </Button>
          )}
        </div>

        {loading && <p className="text-gray-500 text-sm">Loading...</p>}
        {error && <p className="text-red-600 text-sm">{error}</p>}

        {files.map((entry) => (
          <div
            key={entry.name}
            className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50 border border-gray-200"
          >
            <button
              onClick={() => navigateTo(entry)}
              className="flex items-center gap-2 text-left flex-1 min-w-0"
              disabled={!entry.is_directory}
            >
              <span>{entry.is_directory ? '\u{1F4C1}' : '\u{1F4C4}'}</span>
              <span className="text-sm text-gray-900 truncate">{entry.name}</span>
              {!entry.is_directory && (
                <span className="text-xs text-gray-500">
                  {(entry.size / 1024).toFixed(1)} KB
                </span>
              )}
            </button>
            {!entry.is_directory && (
              <Button size="sm" variant="secondary" onClick={() => printFile(entry)}>
                Print
              </Button>
            )}
          </div>
        ))}

        {!loading && files.length === 0 && !error && (
          <p className="text-gray-500 text-sm">Empty directory.</p>
        )}
      </div>
    </Card>
  );
}

// --- Cloud Browser ---

const providerLabels: Record<string, string> = {
  gdrive: 'Google Drive',
  dropbox: 'Dropbox',
};

function CloudBrowser() {
  const [providers, setProviders] = useState<CloudProvider[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<CloudProvider | null>(null);
  const [files, setFiles] = useState<CloudFileEntry[]>([]);
  const [folderStack, setFolderStack] = useState<{ id: string; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingProviders, setLoadingProviders] = useState(true);

  useEffect(() => {
    listProviders()
      .then(setProviders)
      .catch(() => {})
      .finally(() => setLoadingProviders(false));
  }, []);

  const browseFolder = async (provider: CloudProvider, folderId?: string, path?: string) => {
    setLoading(true);
    setError(null);
    setSelectedProvider(provider);

    try {
      const params: { folder_id?: string; path?: string } = {};
      if (provider.provider === 'gdrive' && folderId) {
        params.folder_id = folderId;
      } else if (provider.provider === 'dropbox') {
        params.path = path || '';
      }

      const data = await listFiles(provider.id, params);
      setFiles(data);
    } catch {
      setError('Failed to browse cloud storage');
      setFiles([]);
    } finally {
      setLoading(false);
    }
  };

  const openFolder = (entry: CloudFileEntry) => {
    if (!selectedProvider || !entry.is_directory) return;
    setFolderStack((prev) => [...prev, { id: entry.id, name: entry.name }]);
    browseFolder(selectedProvider, entry.id, entry.id);
  };

  const goUp = () => {
    if (!selectedProvider || folderStack.length === 0) return;
    const newStack = folderStack.slice(0, -1);
    setFolderStack(newStack);
    const parentId = newStack.length > 0 ? newStack[newStack.length - 1].id : undefined;
    browseFolder(selectedProvider, parentId, parentId);
  };

  const goToRoot = () => {
    setSelectedProvider(null);
    setFiles([]);
    setFolderStack([]);
    setError(null);
  };

  const printCloudFile = async (entry: CloudFileEntry) => {
    if (!selectedProvider) return;
    try {
      const isDropbox = selectedProvider.provider === 'dropbox';
      const url = getDownloadUrl(selectedProvider.id, entry.id, isDropbox);
      const response = await api.get(url, { responseType: 'blob' });
      const file = new File([response.data], entry.name);
      const formData = new FormData();
      formData.append('file', file);
      formData.append('copies', '1');
      formData.append('duplex', 'false');
      formData.append('media', 'A4');
      formData.append('hold', 'true');
      await api.post('/jobs/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      alert('File added to print queue (held)');
    } catch {
      alert('Failed to print file');
    }
  };

  const formatSize = (bytes: number | null): string => {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  if (!selectedProvider) {
    return (
      <Card title="Cloud Storage">
        {loadingProviders ? (
          <p className="text-gray-500 text-sm">Loading...</p>
        ) : providers.length === 0 ? (
          <p className="text-gray-500 text-sm">
            No cloud storage connected. Go to Settings to connect Google Drive or Dropbox.
          </p>
        ) : (
          <div className="space-y-2">
            {providers.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  setFolderStack([]);
                  browseFolder(p);
                }}
                className="w-full flex items-center gap-3 p-3 rounded-lg hover:bg-gray-50 border border-gray-200 text-left"
              >
                <span className="text-lg">{p.provider === 'gdrive' ? '\u{2601}' : '\u{1F4E6}'}</span>
                <div>
                  <div className="font-medium text-gray-900">
                    {providerLabels[p.provider] || p.provider}
                  </div>
                  <div className="text-xs text-gray-500">
                    Connected {new Date(p.connected_at).toLocaleDateString()}
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </Card>
    );
  }

  const currentFolder = folderStack.length > 0
    ? folderStack[folderStack.length - 1].name
    : 'Root';

  return (
    <Card title={`${providerLabels[selectedProvider.provider]} - ${currentFolder}`}>
      <div className="space-y-2">
        <div className="flex gap-2 mb-4">
          <Button size="sm" variant="secondary" onClick={goToRoot}>
            All Providers
          </Button>
          {folderStack.length > 0 && (
            <Button size="sm" variant="ghost" onClick={goUp}>
              Up
            </Button>
          )}
        </div>

        {loading && <p className="text-gray-500 text-sm">Loading...</p>}
        {error && <p className="text-red-600 text-sm">{error}</p>}

        {files.map((entry) => (
          <div
            key={entry.id}
            className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50 border border-gray-200"
          >
            <button
              onClick={() => openFolder(entry)}
              className="flex items-center gap-2 text-left flex-1 min-w-0"
              disabled={!entry.is_directory}
            >
              <span>{entry.is_directory ? '\u{1F4C1}' : '\u{1F4C4}'}</span>
              <span className="text-sm text-gray-900 truncate">{entry.name}</span>
              {!entry.is_directory && entry.size && (
                <span className="text-xs text-gray-500">{formatSize(entry.size)}</span>
              )}
            </button>
            {!entry.is_directory && (
              <Button size="sm" variant="secondary" onClick={() => printCloudFile(entry)}>
                Print
              </Button>
            )}
          </div>
        ))}

        {!loading && files.length === 0 && !error && (
          <p className="text-gray-500 text-sm">Empty folder.</p>
        )}
      </div>
    </Card>
  );
}
