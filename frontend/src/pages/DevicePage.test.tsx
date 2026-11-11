import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../test/mocks/server';
import DevicePage from './DevicePage';

function renderAt(url: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <DevicePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lookup = {
  client_name: 'Lobby kiosk',
  scopes: ['print', 'scan', 'files', 'email'],
  token_ttl_minutes: 15,
  expires_at: '2026-11-10T12:00:00Z',
  status: 'pending',
};

describe('DevicePage', () => {
  it('shows the kiosk and scopes, then approves', async () => {
    let body: unknown = null;
    server.use(
      http.get('/api/v1/auth/device/ABCD-EFGH', () => HttpResponse.json(lookup)),
      http.post('/api/v1/auth/device/approve', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ status: 'approved' });
      }),
    );
    renderAt('/device?code=abcd-efgh');
    await waitFor(() => expect(screen.getByText('Lobby kiosk')).toBeInTheDocument());
    expect(screen.getByText('Send email')).toBeInTheDocument();
    expect(screen.getByText(/15 minutes/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(screen.getByText('Approved')).toBeInTheDocument());
    expect(body).toEqual({ user_code: 'ABCD-EFGH', approve: true });
  });

  it('denies', async () => {
    server.use(
      http.get('/api/v1/auth/device/ABCD-EFGH', () => HttpResponse.json(lookup)),
      http.post('/api/v1/auth/device/approve', () => HttpResponse.json({ status: 'denied' })),
    );
    renderAt('/device?code=ABCD-EFGH');
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(screen.getByText('Denied')).toBeInTheDocument());
  });

  it('prompts for a code when none is given and shows not-found for a bad one', async () => {
    server.use(
      http.get('/api/v1/auth/device/ZZZZ-ZZZZ', () =>
        HttpResponse.json({ detail: 'nope', code: 'not_found' }, { status: 404 })),
    );
    renderAt('/device');
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/code shown/i), 'zzzz-zzzz');
    await user.click(screen.getByRole('button', { name: 'Continue' }));
    await waitFor(() => expect(screen.getByText('Code not found')).toBeInTheDocument());
  });
});
