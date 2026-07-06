import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/mocks/server';
import { updateSettings } from '../../api/settings';
import type { AppSettings, SaveControls } from './shared';
import AlertsCard from './AlertsCard';

/** Minimal stand-in for SettingsPage's own draft/save state, scoped to one card. */
function Harness({ initial }: { initial: AppSettings }) {
  const [appSettings, setAppSettings] = useState<AppSettings>(initial);
  const [status, setStatus] = useState<SaveControls['status']>({});

  const set = (key: string) => (value: string) => {
    setAppSettings((prev) => ({ ...prev, [key]: value }));
  };

  const onSave = async (section: string, keys: string[]) => {
    setStatus((s) => ({ ...s, [section]: 'saving' }));
    try {
      const payload = Object.fromEntries(keys.map((k) => [k, String(appSettings[k] ?? '')]));
      await updateSettings(payload);
      setStatus((s) => ({ ...s, [section]: 'saved' }));
    } catch {
      setStatus((s) => ({ ...s, [section]: 'error' }));
    }
  };

  const save: SaveControls = { status, onSave, disabled: false };
  return <AlertsCard appSettings={appSettings} set={set} save={save} />;
}

describe('AlertsCard', () => {
  it('shows the draft values for each field', async () => {
    const user = userEvent.setup();
    render(
      <Harness
        initial={{
          alerts_enabled: true,
          alert_toner_threshold: 15,
          alert_email: 'ops@example.com',
          alert_poll_minutes: 10,
        }}
      />
    );

    // The card is collapsible and starts closed — open it first.
    await user.click(screen.getByText('Supply & Error Alerts'));

    expect(screen.getByRole('switch', { name: 'Enable supply & error alerts' })).toHaveAttribute(
      'aria-checked',
      'true'
    );
    expect(screen.getByDisplayValue('15')).toBeInTheDocument();
    expect(screen.getByDisplayValue('ops@example.com')).toBeInTheDocument();
    expect(screen.getByDisplayValue('10')).toBeInTheDocument();
  });

  it('hides the detail fields until alerts are enabled', async () => {
    const user = userEvent.setup();
    render(
      <Harness
        initial={{
          alerts_enabled: false,
          alert_toner_threshold: 20,
          alert_email: '',
          alert_poll_minutes: 5,
        }}
      />
    );

    await user.click(screen.getByText('Supply & Error Alerts'));

    expect(screen.queryByText('Toner alert threshold (%)')).not.toBeInTheDocument();
  });

  it('saves the section: PUTs exactly the four alert keys', async () => {
    let putBody: Record<string, string> | null = null;
    server.use(
      http.put('/api/settings', async ({ request }) => {
        putBody = (await request.json()) as Record<string, string>;
        return HttpResponse.json({});
      })
    );

    const user = userEvent.setup();
    render(
      <Harness
        initial={{
          alerts_enabled: true,
          alert_toner_threshold: 20,
          alert_email: 'admin@example.com',
          alert_poll_minutes: 5,
        }}
      />
    );

    await user.click(screen.getByText('Supply & Error Alerts'));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody).toEqual({
      alerts_enabled: 'true',
      alert_toner_threshold: '20',
      alert_email: 'admin@example.com',
      alert_poll_minutes: '5',
    });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Saved ✓' })).toBeInTheDocument()
    );
  });
});
