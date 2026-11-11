import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { Check, MonitorSmartphone, X } from 'lucide-react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import ErrorState from '../components/common/ErrorState';
import Skeleton from '../components/common/Skeleton';
import { useDeviceCode } from '../api/queries';
import { decideDevice } from '../api/device';

const SCOPE_LABELS: Record<string, string> = {
  print: 'Print documents',
  scan: 'Scan and view scans',
  files: 'Access files',
  email: 'Send email',
};

function Approval({ code }: { code: string }) {
  const { data, isLoading, isError, refetch } = useDeviceCode(code);
  const decide = useMutation({
    mutationFn: (approve: boolean) => decideDevice(code, approve),
    meta: { suppressGlobalError: true },
  });

  if (isLoading) return <Skeleton variant="card" />;
  if (isError || !data) {
    return (
      <ErrorState
        title="Code not found"
        detail="This code is unknown or has expired. Check the code on the device, or ask it to show a new one."
        onRetry={() => void refetch()}
      />
    );
  }
  if (decide.isSuccess) {
    const approved = decide.data.status === 'approved';
    return (
      <div className="flex flex-col items-center px-6 py-10 text-center" role="status">
        {approved ? (
          <Check className="h-8 w-8 text-ink-600" aria-hidden="true" />
        ) : (
          <X className="h-8 w-8 text-red-500" aria-hidden="true" />
        )}
        <h3 className="mt-3 text-base font-semibold text-gray-900 dark:text-gray-100">
          {approved ? 'Approved' : 'Denied'}
        </h3>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          {approved ? `${data.client_name} is now signed in as you.` : 'The device was not given access.'}
        </p>
      </div>
    );
  }
  if (data.status !== 'pending') {
    return <ErrorState title="Already handled" detail="This request has already been approved or denied." />;
  }

  return (
    <div className="space-y-5">
      <p className="text-sm text-gray-700 dark:text-gray-300">
        <span className="font-semibold">{data.client_name}</span> wants to sign in as you.
        Code <span className="font-mono">{code}</span>
      </p>
      <ul className="space-y-1.5 text-sm text-gray-700 dark:text-gray-300">
        {data.scopes.map((s) => (
          <li key={s} className="flex items-center gap-2">
            <Check className="h-4 w-4 text-ink-600" aria-hidden="true" />
            {SCOPE_LABELS[s] ?? s}
          </li>
        ))}
      </ul>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Access expires after {data.token_ttl_minutes} minutes.
      </p>
      {decide.isError && (
        <p className="text-xs text-red-600 dark:text-red-400" role="alert">
          Could not record your choice. Try again.
        </p>
      )}
      <div className="flex gap-3">
        <Button onClick={() => decide.mutate(true)} disabled={decide.isPending}>Approve</Button>
        <Button variant="secondary" onClick={() => decide.mutate(false)} disabled={decide.isPending}>
          Deny
        </Button>
      </div>
    </div>
  );
}

export default function DevicePage() {
  const [params, setParams] = useSearchParams();
  const code = (params.get('code') ?? '').trim().toUpperCase();
  const [input, setInput] = useState('');

  return (
    <div className="mx-auto max-w-md">
      <Card title="Sign in a device" description="Approve a kiosk or other device to act as you.">
        <div className="p-6">
          {code ? (
            <Approval code={code} />
          ) : (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (input.trim()) setParams({ code: input.trim() });
              }}
            >
              <label htmlFor="user-code" className="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-300">
                <MonitorSmartphone className="h-4 w-4" aria-hidden="true" />
                Code shown on the device
              </label>
              <input
                id="user-code"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="XXXX-XXXX"
                autoComplete="off"
                autoCapitalize="characters"
                className="w-full rounded-lg border border-gray-300 bg-white p-2.5 font-mono text-sm uppercase dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
              />
              <Button type="submit" disabled={!input.trim()}>Continue</Button>
            </form>
          )}
        </div>
      </Card>
    </div>
  );
}
