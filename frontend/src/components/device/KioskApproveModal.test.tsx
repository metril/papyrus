import { describe, it, expect } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/mocks/server';
import KioskApproveModal from './KioskApproveModal';
import { applyNotificationEvent } from '../../hooks/useRealtimeBridge';
import { useToastStore } from '../../store/toastStore';

const future = () => new Date(Date.now() + 120_000).toISOString();
const req = { id: 'r1', client_name: 'Lobby kiosk', choices: [12, 47, 83], expires_at: future() };

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><KioskApproveModal /></QueryClientProvider>);
  return client;
}

describe('KioskApproveModal', () => {
  it('renders nothing without requests', async () => {
    setup();
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows a request found on load and approves with the picked number', async () => {
    let body: unknown;
    server.use(
      http.get('/api/v1/auth/device/requests', () => HttpResponse.json([req])),
      http.post('/api/v1/auth/device/requests/r1/approve', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ status: 'approved' });
      }),
    );
    setup();
    expect(await screen.findByText('Lobby kiosk wants to sign you in')).toBeInTheDocument();
    for (const n of ['12', '47', '83']) expect(screen.getByRole('button', { name: n })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '47' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(body).toEqual({ match_code: 47 });
  });

  it('closes and reports a wrong pick', async () => {
    server.use(
      http.get('/api/v1/auth/device/requests', () => HttpResponse.json([req])),
      http.post('/api/v1/auth/device/requests/r1/approve', () =>
        HttpResponse.json({ detail: 'x', code: 'match_code_mismatch' }, { status: 409 })),
    );
    setup();
    await userEvent.click(await screen.findByRole('button', { name: '12' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(useToastStore.getState().toasts.some((t) => /did not match/.test(t.message))).toBe(true);
  });

  it('denies', async () => {
    let denied = false;
    server.use(
      http.get('/api/v1/auth/device/requests', () => HttpResponse.json([req])),
      http.post('/api/v1/auth/device/requests/r1/deny', () => { denied = true; return HttpResponse.json({ status: 'denied' }); }),
    );
    setup();
    await userEvent.click(await screen.findByRole('button', { name: 'Deny' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(denied).toBe(true);
  });

  it('follows websocket events through the cache', async () => {
    const client = setup();
    await new Promise((r) => setTimeout(r, 20));
    act(() => applyNotificationEvent(client, { type: 'kiosk_login_request', data: { ...req } }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    act(() => applyNotificationEvent(client, { type: 'kiosk_login_resolved', data: { id: 'r1' } }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
