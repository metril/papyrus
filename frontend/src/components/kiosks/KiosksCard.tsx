import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { MonitorSmartphone } from 'lucide-react';
import Card from '../common/Card';
import Button from '../common/Button';
import EmptyState from '../common/EmptyState';
import Skeleton from '../common/Skeleton';
import ErrorState from '../common/ErrorState';
import TokenOnceDialog from './TokenOnceDialog';
import { inputCls } from './fields';
import { createKiosk, deleteKiosk, rotateKioskToken } from '../../api/kiosks';
import { queryKeys, useKiosks } from '../../api/queries';
import { useToast } from '../../hooks/useToast';
import type { KioskTokenResponse } from '../../types';

function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'never';
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export default function KiosksCard() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data: kiosks = [], isLoading, isError, refetch } = useKiosks();
  const [name, setName] = useState('');
  const [minted, setMinted] = useState<{ name: string; token: string } | null>(null);
  const invalidate = () => queryClient.invalidateQueries({ queryKey: queryKeys.kiosks });
  const errToast = (msg: string) => (e: unknown) =>
    toast.show((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || msg);

  const showToken = (r: KioskTokenResponse) => setMinted({ name: r.kiosk.name, token: r.token });

  const create = useMutation({
    mutationFn: () => createKiosk(name.trim()),
    meta: { suppressGlobalError: true },
    onSuccess: (r) => { setName(''); showToken(r); invalidate(); },
    onError: errToast('Failed to create kiosk'),
  });
  const rotate = useMutation({
    mutationFn: (id: string) => rotateKioskToken(id),
    meta: { suppressGlobalError: true },
    onSuccess: (r) => { showToken(r); invalidate(); },
    onError: errToast('Failed to rotate token'),
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteKiosk(id),
    meta: { suppressGlobalError: true },
    onSuccess: invalidate,
    onError: errToast('Failed to delete kiosk'),
  });

  return (
    <Card title="Kiosks" description="Each kiosk is a service user with its own token (print, scan, device).">
      <div className="space-y-4">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
          <input aria-label="New kiosk name" className={inputCls} placeholder="e.g. Front desk" value={name}
            onChange={(e) => setName(e.target.value)} />
          <Button type="submit" disabled={!name.trim() || create.isPending}>Create</Button>
        </form>
        {isLoading && <Skeleton variant="row" count={2} />}
        {isError && <ErrorState title="Could not load kiosks" onRetry={() => refetch()} />}
        {!isLoading && !isError && kiosks.length === 0 && (
          <EmptyState icon={MonitorSmartphone} title="No kiosks yet" hint="Create one to get a token for the kiosk device." />
        )}
        <div className="space-y-2">
          {kiosks.map((k) => (
            <div key={k.id} className="flex flex-wrap items-center justify-between gap-2 p-3 rounded-lg border border-gray-200 dark:border-gray-700">
              <div>
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{k.name}</div>
                <div className="font-mono text-xs text-gray-500 dark:text-gray-400">
                  Last seen: {relativeTime(k.last_seen_at)}
                </div>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" onClick={() => rotate.mutate(k.id)}>Rotate token</Button>
                <Button size="sm" variant="danger-ghost" onClick={() => remove.mutate(k.id)}>Delete</Button>
              </div>
            </div>
          ))}
        </div>
      </div>
      {minted && <TokenOnceDialog token={minted.token} kioskName={minted.name} onClose={() => setMinted(null)} />}
    </Card>
  );
}
