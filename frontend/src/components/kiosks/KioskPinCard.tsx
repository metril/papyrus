import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import Card from '../common/Card';
import Button from '../common/Button';
import { inputCls } from './fields';
import { queryKeys, useKioskPinStatus } from '../../api/queries';
import { clearKioskPin, setKioskPin } from '../../api/device';
import { useToast } from '../../hooks/useToast';

/** Lets the signed-in user set or clear the PIN they can use to sign in at a kiosk. */
export default function KioskPinCard() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data } = useKioskPinStatus();
  const [pin, setPin] = useState('');
  const length = data?.pin_length ?? 6;
  const valid = new RegExp(`^\\d{${length}}$`).test(pin);
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.kioskPin });

  const set = useMutation({
    mutationFn: () => setKioskPin(pin),
    meta: { suppressGlobalError: true },
    onSuccess: () => { setPin(''); toast.show('Kiosk PIN saved', 'success'); refresh(); },
    onError: () => toast.show('Could not save the PIN'),
  });
  const clear = useMutation({
    mutationFn: () => clearKioskPin(),
    meta: { suppressGlobalError: true },
    onSuccess: () => { toast.show('Kiosk PIN removed', 'success'); refresh(); },
    onError: () => toast.show('Could not remove the PIN'),
  });

  return (
    <Card title="Kiosk PIN" description="A numeric PIN to sign in at a kiosk without your phone.">
      <div className="space-y-3">
        {data && !data.enabled && (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            PIN sign-in is currently turned off by an administrator. You can still set a PIN for when it is enabled.
          </p>
        )}
        <p className="text-sm text-gray-700 dark:text-gray-300">
          {data?.has_pin ? 'You have a PIN set.' : 'You have no PIN set.'}
        </p>
        <form
          className="flex flex-wrap gap-2 items-center"
          onSubmit={(e) => { e.preventDefault(); if (valid) set.mutate(); }}
        >
          <input
            aria-label="New kiosk PIN"
            type="password"
            inputMode="numeric"
            autoComplete="off"
            maxLength={length}
            className={`${inputCls} max-w-48 font-mono`}
            placeholder={`${length} digits`}
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))}
          />
          <Button type="submit" size="sm" disabled={!valid || set.isPending}>
            {data?.has_pin ? 'Change PIN' : 'Set PIN'}
          </Button>
          {data?.has_pin && (
            <Button type="button" size="sm" variant="danger-ghost" disabled={clear.isPending} onClick={() => clear.mutate()}>
              Remove PIN
            </Button>
          )}
        </form>
      </div>
    </Card>
  );
}
