import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { server } from '../test/mocks/server';
import SettingsPage from './SettingsPage';
import { useAuthStore } from '../store/authStore';

function makeWrapper(entry = '/settings') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

// SettingsPage also mounts several self-fetching cards (Printers, Scanners,
// CloudStorage, Webhooks, ApiTokens, EmailWebhook) that aren't part of this
// migration. Stub their GETs so the page mounts cleanly without touching
// those components.
function mockAncillaryEndpoints() {
  server.use(
    http.get('/api/v1/printers', () => HttpResponse.json([])),
    http.get('/api/v1/scanners', () => HttpResponse.json([])),
    http.get('/api/v1/cloud/providers', () => HttpResponse.json({ providers: [] })),
    http.get('/api/v1/webhooks', () => HttpResponse.json([])),
    http.get('/api/v1/webhooks/events', () => HttpResponse.json([])),
    http.get('/api/v1/auth/tokens', () => HttpResponse.json([])),
    http.get('/api/v1/email/webhook-info', () =>
      HttpResponse.json({ webhook_url: 'https://papyrus.test/api/email/receive', configured: false })
    ),
  );
}

const adminUser = { id: '1', email: 'a@x.io', display_name: 'A', role: 'admin' } as const;
const plainUser = { id: '2', email: 'u@x.io', display_name: 'U', role: 'user' } as const;

describe('SettingsPage', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: adminUser });
    mockAncillaryEndpoints();
  });

  it('shows a spinner while loading, then feeds fetched settings into the value cards', async () => {
    server.use(
      http.get('/api/v1/settings', () => HttpResponse.json({ base_url: 'https://old.example.com' })),
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
      http.get('/api/v1/settings', () =>
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
      http.get('/api/v1/settings', () => {
        getCount += 1;
        return HttpResponse.json(
          getCount === 1
            ? { base_url: 'https://old.example.com' }
            : { base_url: 'https://new.example.com' }
        );
      }),
      http.put('/api/v1/settings', async ({ request }) => {
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

  describe('tab deep links', () => {
    const admin = { id: '1', email: 'a@x.io', display_name: 'A', role: 'admin' } as const;

    beforeEach(() => {
      server.use(
        http.get('/api/v1/settings', () => HttpResponse.json({})),
        http.get('/api/v1/kiosks', () => HttpResponse.json([])),
      );
    });

    it('opens the Kiosks tab from ?tab=kiosks and syncs clicks to the URL', async () => {
      useAuthStore.setState({ user: admin });
      render(<SettingsPage />, { wrapper: makeWrapper('/settings?tab=kiosks') });
      expect(await screen.findByLabelText('New kiosk name')).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: 'Kiosks' })).toHaveAttribute('aria-selected', 'true');
      await userEvent.click(screen.getByRole('tab', { name: 'General' }));
      expect(screen.queryByLabelText('New kiosk name')).not.toBeInTheDocument();
      expect(screen.getByRole('tab', { name: 'General' })).toHaveAttribute('aria-selected', 'true');
    });

    it('falls back to General for an unknown tab id', async () => {
      useAuthStore.setState({ user: admin });
      render(<SettingsPage />, { wrapper: makeWrapper('/settings?tab=bogus') });
      expect(await screen.findByRole('tab', { name: 'General' })).toHaveAttribute('aria-selected', 'true');
      expect(screen.queryByLabelText('New kiosk name')).not.toBeInTheDocument();
    });
  });

  describe('Address book tab', () => {
    type C = { id: string; name: string; email: string; created_at: string; updated_at: string };
    let store: C[];
    beforeEach(() => {
      store = [{ id: 'c1', name: 'Ann', email: 'ann@x.io', created_at: '', updated_at: '' }];
      server.use(
        http.get('/api/v1/settings', () => HttpResponse.json({})),
        http.get('/api/v1/contacts', () => HttpResponse.json(store)),
        http.post('/api/v1/contacts', async ({ request }) => {
          const b = (await request.json()) as { name: string; email: string };
          const c = { id: 'c2', created_at: '', updated_at: '', ...b };
          store = [...store, c];
          return HttpResponse.json(c, { status: 201 });
        }),
        http.delete('/api/v1/contacts/:id', ({ params }) => {
          store = store.filter((c) => c.id !== params.id);
          return new HttpResponse(null, { status: 204 });
        }),
      );
    });

    it('lists, adds and deletes own contacts from ?tab=contacts', async () => {
      render(<SettingsPage />, { wrapper: makeWrapper('/settings?tab=contacts') });
      expect(await screen.findByText('Ann')).toBeInTheDocument();
      await userEvent.type(screen.getByLabelText('Contact name'), 'Bob');
      await userEvent.type(screen.getByLabelText('Contact email'), 'bob@x.io');
      await userEvent.click(screen.getByRole('button', { name: 'Add' }));
      expect(await screen.findByText('bob@x.io')).toBeInTheDocument();
      await userEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0]);
      await waitFor(() => expect(screen.queryByText('Ann')).not.toBeInTheDocument());
    });

    it('shows only Address book to a non-admin and defaults to it', async () => {
      useAuthStore.setState({ user: plainUser });
      render(<SettingsPage />, { wrapper: makeWrapper('/settings?tab=kiosks') });
      expect(await screen.findByText('Ann')).toBeInTheDocument();
      expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Address book', 'Account']);
      expect(screen.queryByText('Loading settings...')).not.toBeInTheDocument();
    });
  });
});
