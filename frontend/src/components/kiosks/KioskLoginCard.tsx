import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import Card from '../common/Card';
import Button from '../common/Button';
import Toggle from '../common/Toggle';
import { inputCls } from './fields';
import { queryKeys, useSettingsQuery } from '../../api/queries';
import { updateSettings } from '../../api/settings';
import { useToast } from '../../hooks/useToast';

const truthy = (v: unknown) => v === true || v === 'true';

/** Admin switches for kiosk sign-in methods. Toggles save immediately; changing the PIN
 * length asks for an inline confirmation first because it removes every user's PIN. */
export default function KioskLoginCard() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data } = useSettingsQuery();
  const current = Number(data?.kiosk_pin_length ?? 6) || 6;
  const [draft, setDraft] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const save = useMutation({
    mutationFn: (payload: Record<string, string>) => updateSettings(payload),
    meta: { suppressGlobalError: true },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.settings }),
    onError: (e) =>
      toast.show((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Failed to save'),
  });

  const shown = draft ?? String(current);
  const len = Number(shown);
  const valid = Number.isInteger(len) && len >= 4 && len <= 12;
  const changed = draft !== null && valid && len !== current;

  return (
    <Card title="Kiosk sign-in" description="How users can sign in at a kiosk besides scanning a QR code.">
      <div className="space-y-4">
        <Toggle
          checked={truthy(data?.kiosk_approve_enabled)}
          onChange={(v) => save.mutate({ kiosk_approve_enabled: String(v) })}
          label="Tap to approve on my phone"
        />
        <Toggle
          checked={truthy(data?.kiosk_pin_enabled)}
          onChange={(v) => save.mutate({ kiosk_pin_enabled: String(v) })}
          label="PIN sign-in"
        />
        <div className="space-y-2">
          <label htmlFor="kiosk-pin-length" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
            PIN length (4-12 digits)
          </label>
          <div className="flex gap-2 items-center">
            <input
              id="kiosk-pin-length"
              type="number"
              min={4}
              max={12}
              className={`${inputCls} max-w-24`}
              value={shown}
              onChange={(e) => { setDraft(e.target.value); setConfirming(false); }}
            />
            <Button size="sm" disabled={!changed || save.isPending} onClick={() => setConfirming(true)}>
              Save length
            </Button>
          </div>
          {draft !== null && !valid && (
            <p className="text-xs text-red-600 dark:text-red-400">Enter a whole number from 4 to 12.</p>
          )}
          {confirming && (
            <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30 p-3 text-sm space-y-2">
              <p className="text-amber-900 dark:text-amber-200">
                Changing the length removes every user&apos;s kiosk PIN. They will need to set a new one.
              </p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="danger"
                  disabled={save.isPending}
                  onClick={() =>
                    save.mutate({ kiosk_pin_length: String(len) }, {
                      onSuccess: () => { setConfirming(false); setDraft(null); },
                    })
                  }
                >
                  Change length and clear PINs
                </Button>
                <Button size="sm" variant="secondary" onClick={() => { setConfirming(false); setDraft(null); }}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
