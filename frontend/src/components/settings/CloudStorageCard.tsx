import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import Card from '../common/Card';
import Button from '../common/Button';
import { useToast } from '../../hooks/useToast';
import { disconnectProvider, connectWebdav, getAuthorizeUrl } from '../../api/cloud';
import { useCloudProviders, queryKeys } from '../../api/queries';

const providerLabels: Record<string, string> = {
  gdrive: 'Google Drive',
  dropbox: 'Dropbox',
  onedrive: 'OneDrive',
  webdav: 'WebDAV / Nextcloud',
};

export default function CloudStorageCard() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data: cloudProviders = [] } = useCloudProviders();
  const [showWebdav, setShowWebdav] = useState(false);
  const [webdavForm, setWebdavForm] = useState({ url: '', username: '', password: '' });

  const invalidateCloudProviders = () => queryClient.invalidateQueries({ queryKey: queryKeys.cloudProviders });

  const disconnectMutation = useMutation({
    mutationFn: (id: number) => disconnectProvider(id),
    meta: { suppressGlobalError: true },
    onSuccess: invalidateCloudProviders,
    onError: () => toast.show('Failed to disconnect provider'),
  });

  const connectWebdavMutation = useMutation({
    mutationFn: (body: typeof webdavForm) => connectWebdav(body),
    meta: { suppressGlobalError: true },
    onSuccess: () => {
      invalidateCloudProviders();
      toast.show('WebDAV connected', 'success');
    },
    onError: () => toast.show('Failed to connect — check URL and credentials'),
  });

  const handleDisconnectCloud = (id: number) => {
    disconnectMutation.mutate(id);
  };

  const handleConnectWebdav = () => {
    if (!webdavForm.url || !webdavForm.username || !webdavForm.password) return;
    connectWebdavMutation.mutate(webdavForm, {
      onSuccess: () => {
        setShowWebdav(false);
        setWebdavForm({ url: '', username: '', password: '' });
      },
    });
  };

  return (
    <Card title="Cloud Storage" collapsible>
      <div className="space-y-4">
        {cloudProviders.length > 0 && (
          <div className="space-y-2">
            {cloudProviders.map((p) => (
              <div key={p.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700">
                <div>
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{providerLabels[p.provider] || p.provider}</div>
                  <div className="text-xs text-gray-500 dark:text-gray-400">Connected {new Date(p.connected_at).toLocaleDateString()}</div>
                </div>
                <Button size="sm" variant="danger" onClick={() => handleDisconnectCloud(p.id)}>Disconnect</Button>
              </div>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <a href={getAuthorizeUrl('gdrive')}><Button size="sm" variant="secondary">Connect Google Drive</Button></a>
          <a href={getAuthorizeUrl('dropbox')}><Button size="sm" variant="secondary">Connect Dropbox</Button></a>
          <a href={getAuthorizeUrl('onedrive')}><Button size="sm" variant="secondary">Connect OneDrive</Button></a>
          <Button size="sm" variant="secondary" onClick={() => setShowWebdav(!showWebdav)}>Connect WebDAV</Button>
        </div>
        {showWebdav && (
          <div className="space-y-3 p-3 border border-gray-200 dark:border-gray-700 rounded-lg">
            <input
              type="url"
              placeholder="https://cloud.example.com/remote.php/dav/files/user"
              value={webdavForm.url}
              onChange={(e) => setWebdavForm({ ...webdavForm, url: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
            />
            <div className="grid grid-cols-2 gap-3">
              <input
                type="text"
                placeholder="Username"
                value={webdavForm.username}
                onChange={(e) => setWebdavForm({ ...webdavForm, username: e.target.value })}
                className="rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
              />
              <input
                type="password"
                placeholder="Password / App Password"
                value={webdavForm.password}
                onChange={(e) => setWebdavForm({ ...webdavForm, password: e.target.value })}
                className="rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
              />
            </div>
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="secondary" onClick={() => setShowWebdav(false)}>Cancel</Button>
              <Button size="sm" onClick={handleConnectWebdav}>Connect</Button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
