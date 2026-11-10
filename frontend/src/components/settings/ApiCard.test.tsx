import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/mocks/server';
import { updateSettings } from '../../api/settings';
import type { AppSettings, SaveControls } from './shared';
import ApiCard from './ApiCard';

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
  return <ApiCard appSettings={appSettings} set={set} save={save} />;
}

describe('ApiCard', () => {
  it('shows limits and hides them when rate limiting is off', async () => {
    const user = userEvent.setup();
    render(
      <Harness
        initial={{ rate_limit_enabled: true, rate_limit_token_rpm: 700, rate_limit_ip_rpm: 250, public_api_docs: false }}
      />
    );
    await user.click(screen.getByText('API'));
    expect(screen.getByDisplayValue('700')).toBeInTheDocument();
    expect(screen.getByDisplayValue('250')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open API docs' })).toHaveAttribute('href', '/docs');

    await user.click(screen.getByRole('switch', { name: 'Enable API rate limiting' }));
    expect(screen.queryByDisplayValue('700')).not.toBeInTheDocument();
  });

  it('saves exactly the four API keys', async () => {
    let putBody: Record<string, string> | null = null;
    server.use(
      http.put('/api/v1/settings', async ({ request }) => {
        putBody = (await request.json()) as Record<string, string>;
        return HttpResponse.json({});
      })
    );
    const user = userEvent.setup();
    render(
      <Harness
        initial={{ rate_limit_enabled: true, rate_limit_token_rpm: 600, rate_limit_ip_rpm: 300, public_api_docs: true }}
      />
    );
    await user.click(screen.getByText('API'));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody).toEqual({
      rate_limit_enabled: 'true',
      rate_limit_token_rpm: '600',
      rate_limit_ip_rpm: '300',
      public_api_docs: 'true',
    });
  });
});
