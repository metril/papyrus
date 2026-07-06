import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import Card from '../common/Card';
import Button from '../common/Button';
import { useToast } from '../../hooks/useToast';
import { createApiToken, revokeApiToken } from '../../api/tokens';
import { useApiTokens, queryKeys } from '../../api/queries';

export default function ApiTokensCard() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data: tokens = [] } = useApiTokens();
  const [newTokenName, setNewTokenName] = useState('');
  const [newTokenPermissions, setNewTokenPermissions] = useState<string[]>([]);
  const [newTokenExpiry, setNewTokenExpiry] = useState<number | null>(null);
  const [createdToken, setCreatedToken] = useState<string | null>(null);

  const allPermissions = ['print', 'scan', 'files', 'admin', 'email'] as const;
  const permissionLabels: Record<string, string> = {
    print: 'Print', scan: 'Scan', files: 'Files', admin: 'Admin', email: 'Email',
  };

  const togglePermission = (perm: string) => {
    setNewTokenPermissions((prev) =>
      prev.includes(perm) ? prev.filter((p) => p !== perm) : [...prev, perm]
    );
  };

  const createMutation = useMutation({
    mutationFn: () => createApiToken({
      name: newTokenName,
      permissions: newTokenPermissions,
      expires_in_days: newTokenExpiry,
    }),
    meta: { suppressGlobalError: true },
    onSuccess: (data) => {
      setCreatedToken(data.token);
      setNewTokenName('');
      setNewTokenPermissions([]);
      setNewTokenExpiry(null);
      queryClient.invalidateQueries({ queryKey: queryKeys.apiTokens });
    },
    onError: () => toast.show('Failed to create token'),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => revokeApiToken(id),
    meta: { suppressGlobalError: true },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.apiTokens }),
    onError: () => toast.show('Failed to revoke token'),
  });

  const createToken = () => {
    if (!newTokenName || newTokenPermissions.length === 0) return;
    createMutation.mutate();
  };

  const revokeToken = (id: string) => {
    revokeMutation.mutate(id);
  };

  return (
    <Card
      title="API Tokens"
      description="Bearer tokens for scripted access to the Papyrus API."
      collapsible
    >
      <div className="space-y-4">
        <div className="space-y-3 p-3 rounded-lg border border-gray-200 dark:border-gray-700">
          <input
            type="text"
            placeholder="Token name"
            value={newTokenName}
            onChange={(e) => setNewTokenName(e.target.value)}
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
          />
          <div>
            <p className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">Permissions</p>
            <div className="flex flex-wrap gap-2">
              {allPermissions.map((perm) => (
                <label key={perm} className="flex items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={newTokenPermissions.includes(perm)}
                    onChange={() => togglePermission(perm)}
                    className="rounded border-gray-300 dark:border-gray-600"
                  />
                  <span className="text-gray-700 dark:text-gray-300">{permissionLabels[perm]}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div>
              <p className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Expires</p>
              <select
                value={newTokenExpiry ?? ''}
                onChange={(e) => setNewTokenExpiry(e.target.value ? Number(e.target.value) : null)}
                className="rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
              >
                <option value="">Never</option>
                <option value="7">7 days</option>
                <option value="30">30 days</option>
                <option value="90">90 days</option>
                <option value="365">1 year</option>
              </select>
            </div>
            <div className="self-end">
              <Button size="sm" onClick={createToken} disabled={!newTokenName || newTokenPermissions.length === 0}>Create</Button>
            </div>
          </div>
        </div>
        {createdToken && (
          <div className="bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900/50 rounded-lg p-3">
            <p className="text-sm text-amber-800 dark:text-amber-300 font-medium">
              Token created! Copy it now &mdash; it won&apos;t be shown again:
            </p>
            {/* select-all: one click/tap selects the whole token for copying. */}
            <code className="font-mono text-xs break-all block mt-1 bg-amber-100 dark:bg-amber-900/40 text-amber-900 dark:text-amber-200 p-2 rounded-lg select-all cursor-text">{createdToken}</code>
          </div>
        )}
        {tokens.length === 0 ? (
          <p className="text-gray-500 dark:text-gray-400 text-sm">No API tokens created.</p>
        ) : (
          <div className="space-y-2">
            {tokens.map((token) => (
              <div key={token.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700">
                <div>
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{token.name}</div>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {token.permissions.map((p) => (
                      <span key={p} className="font-mono text-xs px-1.5 py-0.5 rounded-full font-medium bg-ink-100 text-ink-700 dark:bg-ink-900/40 dark:text-ink-300">{p}</span>
                    ))}
                  </div>
                  <div className="font-mono text-xs text-gray-500 dark:text-gray-400 mt-1">
                    {token.expires_at ? `Expires: ${new Date(token.expires_at).toLocaleDateString()}` : 'No expiry'}
                    {token.last_used_at && ` · Last used: ${new Date(token.last_used_at).toLocaleDateString()}`}
                  </div>
                </div>
                <Button size="sm" variant="danger" onClick={() => revokeToken(token.id)}>Revoke</Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
