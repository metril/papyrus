import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ChevronRight, Cloud, Eye, FileText, Folder, FolderOpen, Printer } from 'lucide-react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import FilePreviewModal from '../components/common/FilePreviewModal';
import Skeleton from '../components/common/Skeleton';
import EmptyState from '../components/common/EmptyState';
import ErrorState from '../components/common/ErrorState';
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
      <div className="flex flex-wrap items-center gap-4">
        <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Files</h2>
        <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
          <button
            onClick={() => setActiveTab('network')}
            className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
              activeTab === 'network'
                ? 'bg-ink-600 text-white shadow-sm dark:bg-ink-500'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100'
            }`}
          >
            Network
          </button>
          <button
            onClick={() => setActiveTab('cloud')}
            className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
              activeTab === 'cloud'
                ? 'bg-ink-600 text-white shadow-sm dark:bg-ink-500'
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

// --- Breadcrumb trail (shared by both browsers) ---

interface Crumb {
  label: string;
  /** Omitted on the final (current-location) crumb — it renders as plain text. */
  onClick?: () => void;
}

function Breadcrumb({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1.5 font-mono text-xs">
      {items.map((crumb, i) => (
        <span key={i} className="flex items-center gap-1.5">
          {i > 0 && (
            <ChevronRight className="h-3 w-3 text-gray-400 dark:text-gray-600" strokeWidth={1.75} aria-hidden="true" />
          )}
          {crumb.onClick ? (
            <button
              onClick={crumb.onClick}
              className="text-gray-500 hover:text-ink-600 dark:text-gray-400 dark:hover:text-ink-400"
            >
              {crumb.label}
            </button>
          ) : (
            <span className="text-gray-900 dark:text-gray-100">{crumb.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

// --- Network (SMB) Browser ---

function smbFileMeta(entry: SMBFileEntry): string {
  const parts: string[] = [];
  if (!entry.is_directory) parts.push(`${(entry.size / 1024).toFixed(1)} KB`);
  if (entry.modified_at) parts.push(new Date(entry.modified_at).toLocaleDateString());
  return parts.join(' · ');
}

function NetworkBrowser() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [selectedShare, setSelectedShare] = useState<SMBShare | null>(null);
  const [currentPath, setCurrentPath] = useState('/');

  const {
    data: shares = [],
    isPending: sharesLoading,
    isError: sharesError,
    refetch: refetchShares,
  } = useQuery({
    queryKey: queryKeys.smbShares,
    queryFn: listSmbShares,
  });

  // Query cache is keyed by (shareId, path), so revisiting an already-browsed
  // directory (e.g. navigating back up) serves instantly from cache instead of
  // re-fetching.
  const {
    data: files = [],
    isPending: filesLoading,
    isError,
    error: browseError,
    refetch: refetchBrowse,
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

  if (!selectedShare) {
    return (
      <Card title="SMB Shares">
        {sharesLoading ? (
          <Skeleton variant="row" count={3} />
        ) : sharesError ? (
          <ErrorState onRetry={() => refetchShares()} />
        ) : shares.length === 0 ? (
          <EmptyState
            icon={FolderOpen}
            title="No shares configured"
            hint="Add a network share in Settings to browse it here."
          />
        ) : (
          <div className="space-y-2">
            {shares.map((share) => (
              <button
                key={share.id}
                onClick={() => browse(share, '/')}
                className="w-full flex items-center gap-3 p-3 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 border border-gray-200 dark:border-gray-700 text-left"
              >
                <Folder className="h-5 w-5 shrink-0 text-gray-400 dark:text-gray-500" strokeWidth={1.75} aria-hidden="true" />
                <div className="min-w-0">
                  <div className="font-medium text-gray-900 dark:text-gray-100">{share.name}</div>
                  <div className="font-mono text-xs text-gray-500 dark:text-gray-400">
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

  const pathSegments = currentPath.split('/').filter(Boolean);
  const crumbs: Crumb[] = [
    { label: 'All Shares', onClick: () => setSelectedShare(null) },
    {
      label: selectedShare.name,
      onClick: pathSegments.length > 0 ? () => browse(selectedShare, '/') : undefined,
    },
    ...pathSegments.map((segment, i) => ({
      label: segment,
      onClick:
        i < pathSegments.length - 1
          ? () => browse(selectedShare, '/' + pathSegments.slice(0, i + 1).join('/'))
          : undefined,
    })),
  ];

  return (
    <Card>
      <Breadcrumb items={crumbs} />
      <hr className="rule-perf my-4 text-gray-300 dark:text-gray-700" />

      {filesLoading ? (
        <Skeleton variant="row" count={3} />
      ) : error ? (
        <ErrorState detail={error} onRetry={() => refetchBrowse()} />
      ) : files.length === 0 ? (
        <EmptyState icon={Folder} title="Empty directory" />
      ) : (
        <div className="space-y-2">
          {files.map((entry) => (
            <div
              key={entry.name}
              className="flex flex-col gap-3 rounded-lg border border-gray-200 p-3 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800/50 sm:flex-row sm:items-center sm:justify-between"
            >
              <button
                onClick={() => navigateTo(entry)}
                className="flex min-w-0 flex-1 items-center gap-3 text-left"
                disabled={!entry.is_directory}
              >
                {entry.is_directory ? (
                  <Folder className="h-5 w-5 shrink-0 text-gray-400 dark:text-gray-500" strokeWidth={1.75} aria-hidden="true" />
                ) : (
                  <FileText className="h-5 w-5 shrink-0 text-gray-400 dark:text-gray-500" strokeWidth={1.75} aria-hidden="true" />
                )}
                <div className="min-w-0">
                  <div className="truncate text-sm text-gray-900 dark:text-gray-100">{entry.name}</div>
                  {smbFileMeta(entry) && (
                    <div className="font-mono text-xs text-gray-500 dark:text-gray-400">{smbFileMeta(entry)}</div>
                  )}
                </div>
              </button>
              {!entry.is_directory && (
                <Button size="sm" variant="ghost" onClick={() => printMutation.mutate(entry)} className="sm:ml-4 sm:shrink-0">
                  <Printer className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                  Print
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// --- Cloud Browser ---

const providerLabels: Record<string, string> = {
  gdrive: 'Google Drive',
  dropbox: 'Dropbox',
  onedrive: 'OneDrive',
};

function formatCloudSize(bytes: number | null): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function cloudFileMeta(entry: CloudFileEntry): string {
  const parts: string[] = [];
  if (!entry.is_directory && entry.size) parts.push(formatCloudSize(entry.size));
  if (entry.modified_at) parts.push(new Date(entry.modified_at).toLocaleDateString());
  return parts.join(' · ');
}

interface ProviderCardProps {
  provider: CloudProvider;
  onSelect: (provider: CloudProvider) => void;
}

// A connected-provider tile on the shared Card anatomy (rounded-xl, border,
// shadow) rather than the plain bordered rows used for SMB shares/files —
// wrapped in a <button> so the whole tile is a single click target.
function ProviderCard({ provider, onSelect }: ProviderCardProps) {
  return (
    <button
      onClick={() => onSelect(provider)}
      className="rounded-xl text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ink-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-gray-900"
    >
      <Card className="transition-colors hover:border-ink-300 dark:hover:border-ink-700">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-800">
            <Cloud className="h-5 w-5 text-gray-500 dark:text-gray-400" strokeWidth={1.75} aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
              {providerLabels[provider.provider] || provider.provider}
            </div>
            <div className="font-mono text-xs text-gray-500 dark:text-gray-400">
              Connected {new Date(provider.connected_at).toLocaleDateString()}
            </div>
          </div>
        </div>
      </Card>
    </button>
  );
}

function CloudBrowser() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const {
    data: providers = [],
    isPending: loadingProviders,
    isError: providersError,
    refetch: refetchProviders,
  } = useCloudProviders();
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
    isPending: filesLoading,
    isError,
    refetch: refetchFiles,
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

  const goToRoot = () => {
    setSelectedProvider(null);
    setFolderStack([]);
  };

  if (!selectedProvider) {
    return (
      <div className="space-y-4">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Cloud Storage</h3>
        {loadingProviders ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {Array.from({ length: 2 }, (_, i) => (
              <Skeleton key={i} variant="card" />
            ))}
          </div>
        ) : providersError ? (
          <ErrorState onRetry={() => refetchProviders()} />
        ) : providers.length === 0 ? (
          <EmptyState
            icon={Cloud}
            title="No cloud storage connected"
            hint="Connect Google Drive, Dropbox, or OneDrive in Settings."
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {providers.map((p) => (
              <ProviderCard
                key={p.id}
                provider={p}
                onSelect={(provider) => {
                  setFolderStack([]);
                  setSelectedProvider(provider);
                }}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  const crumbs: Crumb[] = [
    { label: 'All Providers', onClick: goToRoot },
    {
      label: providerLabels[selectedProvider.provider] || selectedProvider.provider,
      onClick: folderStack.length > 0 ? () => setFolderStack([]) : undefined,
    },
    ...folderStack.map((folder, i) => ({
      label: folder.name,
      onClick:
        i < folderStack.length - 1
          ? () => setFolderStack((prev) => prev.slice(0, i + 1))
          : undefined,
    })),
  ];

  return (
    <Card>
      <Breadcrumb items={crumbs} />
      <hr className="rule-perf my-4 text-gray-300 dark:text-gray-700" />

      {filesLoading ? (
        <Skeleton variant="row" count={3} />
      ) : error ? (
        <ErrorState detail={error} onRetry={() => refetchFiles()} />
      ) : files.length === 0 ? (
        <EmptyState icon={Folder} title="Empty folder" />
      ) : (
        <div className="space-y-2">
          {files.map((entry) => (
            <div
              key={entry.id}
              className="flex flex-col gap-3 rounded-lg border border-gray-200 p-3 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800/50 sm:flex-row sm:items-center sm:justify-between"
            >
              <button
                onClick={() => openFolder(entry)}
                className="flex min-w-0 flex-1 items-center gap-3 text-left"
                disabled={!entry.is_directory}
              >
                {entry.is_directory ? (
                  <Folder className="h-5 w-5 shrink-0 text-gray-400 dark:text-gray-500" strokeWidth={1.75} aria-hidden="true" />
                ) : (
                  <FileText className="h-5 w-5 shrink-0 text-gray-400 dark:text-gray-500" strokeWidth={1.75} aria-hidden="true" />
                )}
                <div className="min-w-0">
                  <div className="truncate text-sm text-gray-900 dark:text-gray-100">{entry.name}</div>
                  {cloudFileMeta(entry) && (
                    <div className="font-mono text-xs text-gray-500 dark:text-gray-400">{cloudFileMeta(entry)}</div>
                  )}
                </div>
              </button>
              {!entry.is_directory && (
                <div className="flex flex-wrap gap-2 sm:ml-4 sm:shrink-0">
                  <Button size="sm" variant="ghost" onClick={() => setPreviewFile(entry)}>
                    <Eye className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                    View
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => printMutation.mutate(entry)}>
                    <Printer className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                    Print
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

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
