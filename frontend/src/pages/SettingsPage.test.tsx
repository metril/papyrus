import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../test/mocks/server';
import SettingsPage from './SettingsPage';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

// SettingsPage also mounts several self-fetching cards (Printers, Scanners,
// CloudStorage, Webhooks, ApiTokens, EmailWebhook) that aren't part of this
// migration. Stub their GETs so the page mounts cleanly without touching
// those components.
function mockAncillaryEndpoints() {
  server.use(
    http.get('/api/printers', () => HttpResponse.json([])),
    http.get('/api/scanners', () => HttpResponse.json([])),
    http.get('/api/cloud/providers', () => HttpResponse.json({ providers: [] })),
    http.get('/api/webhooks', () => HttpResponse.json([])),
    http.get('/api/webhooks/events', () => HttpResponse.json([])),
    http.get('/api/auth/tokens', () => HttpResponse.json([])),
    http.get('/api/email/webhook-info', () =>
      HttpResponse.json({ webhook_url: 'https://papyrus.test/api/email/receive', configured: false })
    ),
  );
}

describe('SettingsPage', () => {
  beforeEach(() => {
    mockAncillaryEndpoints();
  });

  it('shows a spinner while loading, then feeds fetched settings into the value cards', async () => {
    server.use(
      http.get('/api/settings', () => HttpResponse.json({ base_url: 'https://old.example.com' })),
    );

    render(<SettingsPage />, { wrapper: makeWrapper() });

    expect(screen.getByText('Loading settings...')).toBeInTheDocument();

    await waitFor(() =>
      expect(screen.getByDisplayValue('https://old.example.com')).toBeInTheDocument()
    );
    expect(screen.queryByText('Loading settings...')).not.toBeInTheDocument();
  });

  it('shows the error banner copy when the initial load fails', async () => {
    server.use(
      http.get('/api/settings', () =>
        HttpResponse.json({ detail: 'db unavailable' }, { status: 500 })
      ),
    );

    render(<SettingsPage />, { wrapper: makeWrapper() });

    await waitFor(() =>
      expect(screen.getByText('Failed to load settings: db unavailable')).toBeInTheDocument()
    );
  });

  it('saves a section: PUTs only that section\'s keys, then reflects the re-GET into the draft with a saved badge', async () => {
    let putBody: Record<string, string> | null = null;
    let getCount = 0;

    server.use(
      http.get('/api/settings', () => {
        getCount += 1;
        return HttpResponse.json(
          getCount === 1
            ? { base_url: 'https://old.example.com' }
            : { base_url: 'https://new.example.com' }
        );
      }),
      http.put('/api/settings', async ({ request }) => {
        putBody = (await request.json()) as Record<string, string>;
        return HttpResponse.json({});
      }),
    );

    const user = userEvent.setup();
    render(<SettingsPage />, { wrapper: makeWrapper() });

    await waitFor(() =>
      expect(screen.getByDisplayValue('https://old.example.com')).toBeInTheDocument()
    );

    // Scope to the Application card's own content wrapper so we hit its Save
    // button specifically (many cards render a "Save" button).
    const baseUrlInput = screen.getByDisplayValue('https://old.example.com');
    const applicationSection = baseUrlInput.closest('.space-y-3') as HTMLElement;
    const saveButton = within(applicationSection).getByRole('button', { name: 'Save' });

    await user.click(saveButton);

    // Only the Application section's keys should have been sent — nothing
    // from any other card.
    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody).toEqual({
      base_url: 'https://old.example.com',
      dev_mode: '',
      require_release_pin: '',
    });

    await waitFor(() =>
      expect(within(applicationSection).getByRole('button', { name: 'Saved ✓' })).toBeInTheDocument()
    );

    // The re-GET's fresh value flows back into the draft (and thus the input).
    await waitFor(() =>
      expect(screen.getByDisplayValue('https://new.example.com')).toBeInTheDocument()
    );
  });
});
