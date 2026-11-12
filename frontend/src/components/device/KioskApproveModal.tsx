import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { queryKeys, useDeviceRequests } from '../../api/queries';
import { answerDeviceRequest, denyDeviceRequest, type DeviceRequest } from '../../api/device';
import { showToast } from '../../store/toastStore';
import Button from '../common/Button';

/** Tap-to-approve prompt: "<kiosk> wants to sign you in" with the three numbers to pick
 * from. Mounted once in AppShell; fed by the `notifications` channel (via the Query cache)
 * and by a query on load, so a request made while the page was closed still shows. */
export default function KioskApproveModal() {
  const queryClient = useQueryClient();
  const { data: requests = [] } = useDeviceRequests();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const current: DeviceRequest | undefined = requests.find(
    (r) => new Date(r.expires_at).getTime() > now,
  );

  const drop = (id: string) =>
    queryClient.setQueryData<DeviceRequest[]>(queryKeys.deviceRequests, (prev) =>
      prev ? prev.filter((r) => r.id !== id) : prev,
    );

  const pick = useMutation({
    mutationFn: ({ id, code }: { id: string; code: number }) => answerDeviceRequest(id, code),
    meta: { suppressGlobalError: true },
    onSuccess: (_d, v) => {
      drop(v.id);
      showToast('Signed in on the kiosk', 'success');
    },
    onError: (e, v) => {
      drop(v.id);
      const mismatch = axios.isAxiosError(e) && e.response?.data?.code === 'match_code_mismatch';
      showToast(mismatch ? 'That number did not match. The request was denied.' : 'Request is no longer valid', 'error');
    },
  });
  const deny = useMutation({
    mutationFn: (id: string) => denyDeviceRequest(id),
    meta: { suppressGlobalError: true },
    onSettled: (_d, _e, id) => drop(id),
  });

  if (!current) return null;
  const busy = pick.isPending || deny.isPending;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="kiosk-approve-title"
        className="w-full max-w-sm rounded-2xl bg-white dark:bg-gray-900 p-6 shadow-xl space-y-5"
      >
        <div className="space-y-1">
          <h2 id="kiosk-approve-title" className="text-lg font-semibold text-gray-900 dark:text-gray-50">
            {current.client_name} wants to sign you in
          </h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Tap the number shown on the kiosk. If you don&apos;t recognise this, deny it.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {current.choices.map((n) => (
            <button
              key={n}
              type="button"
              disabled={busy}
              onClick={() => pick.mutate({ id: current.id, code: n })}
              className="rounded-xl border border-gray-200 dark:border-gray-700 py-5 font-mono text-3xl font-semibold text-gray-900 dark:text-gray-50 hover:bg-ink-50 dark:hover:bg-ink-950/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ink-500 disabled:opacity-50"
            >
              {n}
            </button>
          ))}
        </div>
        <Button variant="secondary" className="w-full" disabled={busy} onClick={() => deny.mutate(current.id)}>
          Deny
        </Button>
      </div>
    </div>
  );
}
