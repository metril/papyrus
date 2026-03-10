import { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import api from '../api/client';
import type { SMBShare, SMBFileEntry } from '../types';

export default function FilesPage() {
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
      // Download from SMB and create a print job
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

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Network Files</h2>

      {!selectedShare ? (
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
      ) : (
        <Card
          title={`${selectedShare.name} - ${currentPath}`}
        >
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
      )}
    </div>
  );
}
