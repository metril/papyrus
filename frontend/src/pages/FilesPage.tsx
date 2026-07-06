import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import FilePreviewModal from '../components/common/FilePreviewModal';
import { queryKeys, useCloudProviders } from '../api/queries';
import { listSmbShares, browseSmb, downloadSmbFile } from '../api/smb';
import { uploadPrintJob } from '../api/printer';
import { listFiles, getDownloadUrl, downloadCloudFile } from '../api/cloud';
import type { SMBShare, SMBFileEntry, CloudProvider, CloudFileEntry } from '../types';
import { useToast } from '../hooks/useToast';

type Tab = 'network' | 'cloud';

export default function FilesPage() {
  const [activeTab, setActiveTab] = useState<Tab>('network');

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Files</h2>
        <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
          <button
            onClick={() => setActiveTab('network')}
            className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
              activeTab === 'network'
                ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100'
            }`}
          >
            Network
          </button>
          <button
            onClick={() => setActiveTab('cloud')}
            className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
              activeTab === 'cloud'
                ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100'
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
  const toast = useToast();
  const queryClient = useQueryClient();
  const [selectedShare, setSelectedShare] = useState<SMBShare | null>(null);
  const [currentPath, setCurrentPath] = useState('/');

  const { data: shares = [] } = useQuery({
    queryKey: queryKeys.smbShares,
    queryFn: listSmbShares,
  });

  // Query cache is keyed by (shareId, path), so revisiting an already-browsed
  // directory (e.g. navigating back up) serves instantly from cache instead of
  // re-fetching.
  const {
    data: files = [],
    isLoading: loading,
    isError,
    error: browseError,
  } = useQuery({
    queryKey: queryKeys.smbBrowse(selectedShare?.id ?? 0, currentPath),
    queryFn: () => browseSmb(selectedShare!.id, currentPath),
    enabled: !!selectedShare,
  });
  const error = isError
    ? browseError instanceof Error
      ? browseError.message
      : 'Failed to browse share'
    : null;

  const printMutation = useMutation({
    mutationFn: async (entry: SMBFileEntry) => {
      if (!selectedShare) throw new Error('No share selected');
      const filePath = currentPath === '/' ? `/${entry.name}` : `${currentPath}/${entry.name}`;
      const blob = await downloadSmbFile(selectedShare.id, filePath);
      const file = new File([blob], entry.name);
      return uploadPrintJob(file, { copies: 1, duplex: false, media: 'A4', hold: true });
    },
    meta: { suppressGlobalError: true },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
      toast.show('File added to print queue (held)', 'success');
    },
    onError: () => toast.show('Failed to print file'),
  });

  const browse = (share: SMBShare, path: string) => {
    setSelectedShare(share);
    setCurrentPath(path);
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
                className="w-full flex items-center gap-3 p-3 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 border border-gray-200 dark:border-gray-700 text-left"
              >
                <span className="text-lg">&#128193;</span>
                <div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">{share.name}</div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">
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
            className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 border border-gray-200 dark:border-gray-700"
          >
            <button
              onClick={() => navigateTo(entry)}
              className="flex items-center gap-2 text-left flex-1 min-w-0"
              disabled={!entry.is_directory}
            >
              <span>{entry.is_directory ? '\u{1F4C1}' : '\u{1F4C4}'}</span>
              <span className="text-sm text-gray-900 dark:text-gray-100 truncate">{entry.name}</span>
              {!entry.is_directory && (
                <span className="text-xs text-gray-500">
                  {(entry.size / 1024).toFixed(1)} KB
                </span>
              )}
            </button>
            {!entry.is_directory && (
              <Button size="sm" variant="secondary" onClick={() => printMutation.mutate(entry)}>
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
  onedrive: 'OneDrive',
};

function CloudBrowser() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data: providers = [], isLoading: loadingProviders } = useCloudProviders();
  const [selectedProvider, setSelectedProvider] = useState<CloudProvider | null>(null);
  const [folderStack, setFolderStack] = useState<{ id: string; name: string }[]>([]);
  const [previewFile, setPreviewFile] = useState<CloudFileEntry | null>(null);

  const googleExportMimes: Record<string, string> = {
    'application/vnd.google-apps.document': 'application/pdf',
    'application/vnd.google-apps.spreadsheet': 'application/pdf',
    'application/vnd.google-apps.presentation': 'application/pdf',
  };

  const getPreviewMime = (entry: CloudFileEntry): string => {
    if (entry.mime_type && googleExportMimes[entry.mime_type]) {
      return googleExportMimes[entry.mime_type];
    }
    if (entry.mime_type) return entry.mime_type;
    const ext = entry.name.split('.').pop()?.toLowerCase();
    const mimeMap: Record<string, string> = {
      pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg',
      jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp',
    };
    return mimeMap[ext || ''] || 'application/octet-stream';
  };

  const currentFolderId = folderStack.length > 0 ? folderStack[folderStack.length - 1].id : undefined;
  const folderKey = currentFolderId ?? 'root';

  // Query cache is keyed by (providerId, folderKey), so revisiting an
  // already-browsed folder (e.g. navigating back up) serves instantly from
  // cache instead of re-fetching.
  const {
    data: files = [],
    isLoading: loading,
    isError,
  } = useQuery({
    queryKey: queryKeys.cloudFiles(selectedProvider?.id ?? 0, folderKey),
    queryFn: () => {
      const provider = selectedProvider!;
      const params: { folder_id?: string; path?: string } = {};
      if ((provider.provider === 'gdrive' || provider.provider === 'onedrive') && currentFolderId) {
        params.folder_id = currentFolderId;
      } else if (provider.provider === 'dropbox') {
        params.path = currentFolderId || '';
      }
      return listFiles(provider.id, params);
    },
    enabled: !!selectedProvider,
  });
  const error = isError ? 'Failed to browse cloud storage' : null;

  const printMutation = useMutation({
    mutationFn: async (entry: CloudFileEntry) => {
      if (!selectedProvider) throw new Error('No provider selected');
      const isDropbox = selectedProvider.provider === 'dropbox';
      const blob = await downloadCloudFile(
        selectedProvider.id,
        entry.id,
        isDropbox,
        entry.name,
        entry.mime_type || undefined,
      );
      const resolvedMime = getPreviewMime(entry);
      const file = new File([blob], entry.name, { type: resolvedMime });
      return uploadPrintJob(file, { copies: 1, duplex: false, media: 'A4', hold: true });
    },
    meta: { suppressGlobalError: true },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.jobs.list() });
      toast.show('File added to print queue (held)', 'success');
    },
    onError: () => toast.show('Failed to print file'),
  });

  const openFolder = (entry: CloudFileEntry) => {
    if (!selectedProvider || !entry.is_directory) return;
    setFolderStack((prev) => [...prev, { id: entry.id, name: entry.name }]);
  };

  const goUp = () => {
    if (!selectedProvider || folderStack.length === 0) return;
    setFolderStack((prev) => prev.slice(0, -1));
  };

  const goToRoot = () => {
    setSelectedProvider(null);
    setFolderStack([]);
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
            No cloud storage connected. Go to Settings to connect Google Drive, Dropbox, or OneDrive.
          </p>
        ) : (
          <div className="space-y-2">
            {providers.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  setFolderStack([]);
                  setSelectedProvider(p);
                }}
                className="w-full flex items-center gap-3 p-3 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 border border-gray-200 dark:border-gray-700 text-left"
              >
                <span className="text-lg">{p.provider === 'gdrive' ? '\u{2601}' : '\u{1F4E6}'}</span>
                <div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">
                    {providerLabels[p.provider] || p.provider}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">
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
            className="flex items-center justify-between p-3 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 border border-gray-200 dark:border-gray-700"
          >
            <button
              onClick={() => openFolder(entry)}
              className="flex items-center gap-2 text-left flex-1 min-w-0"
              disabled={!entry.is_directory}
            >
              <span>{entry.is_directory ? '\u{1F4C1}' : '\u{1F4C4}'}</span>
              <span className="text-sm text-gray-900 dark:text-gray-100 truncate">{entry.name}</span>
              {!entry.is_directory && entry.size && (
                <span className="text-xs text-gray-500">{formatSize(entry.size)}</span>
              )}
            </button>
            {!entry.is_directory && (
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => setPreviewFile(entry)}>
                  View
                </Button>
                <Button size="sm" variant="secondary" onClick={() => printMutation.mutate(entry)}>
                  Print
                </Button>
              </div>
            )}
          </div>
        ))}

        {!loading && files.length === 0 && !error && (
          <p className="text-gray-500 text-sm">Empty folder.</p>
        )}
      </div>

      {previewFile && selectedProvider && (
        <FilePreviewModal
          url={getDownloadUrl(selectedProvider.id, previewFile.id, selectedProvider.provider === 'dropbox', previewFile.name, previewFile.mime_type || undefined)}
          filename={previewFile.name}
          mimeType={getPreviewMime(previewFile)}
          onClose={() => setPreviewFile(null)}
        />
      )}
    </Card>
  );
}
