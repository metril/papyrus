import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/mocks/server';
import KioskLoginCard from './KioskLoginCard';
import KioskPinCard from './KioskPinCard';

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function settingsServer() {
  const puts: Record<string, string>[] = [];
  server.use(
    http.get('/api/v1/settings', () =>
      HttpResponse.json({ kiosk_approve_enabled: false, kiosk_pin_enabled: true, kiosk_pin_length: 6 })),
    http.put('/api/v1/settings', async ({ request }) => {
      puts.push((await request.json()) as Record<string, string>);
      return HttpResponse.json({ ok: true });
    }),
  );
  return puts;
}

describe('KioskLoginCard', () => {
  it('uses switches (not checkboxes) that save immediately', async () => {
    const puts = settingsServer();
    wrap(<KioskLoginCard />);
    const approve = await screen.findByRole('switch', { name: /tap to approve/i });
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('switch', { name: /PIN sign-in/i })).toHaveAttribute('aria-checked', 'true'));
    expect(approve).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(approve);
    await waitFor(() => expect(puts).toContainEqual({ kiosk_approve_enabled: 'true' }));
  });

  it('asks for confirmation before changing the PIN length', async () => {
    const puts = settingsServer();
    wrap(<KioskLoginCard />);
    const input = await screen.findByLabelText(/PIN length/i);
    await waitFor(() => expect(input).toHaveValue(6));
    await userEvent.clear(input);
    await userEvent.type(input, '8');
    await userEvent.click(screen.getByRole('button', { name: 'Save length' }));
    expect(screen.getByText(/removes every user/i)).toBeInTheDocument();
    expect(puts).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: /change length and clear pins/i }));
    await waitFor(() => expect(puts).toContainEqual({ kiosk_pin_length: '8' }));
  });

  it('cancel discards the length change', async () => {
    const puts = settingsServer();
    wrap(<KioskLoginCard />);
    const input = await screen.findByLabelText(/PIN length/i);
    await userEvent.clear(input);
    await userEvent.type(input, '9');
    await userEvent.click(screen.getByRole('button', { name: 'Save length' }));
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByText(/removes every user/i)).not.toBeInTheDocument();
    expect(puts).toHaveLength(0);
  });
});

describe('KioskPinCard', () => {
  it('sets a numeric PIN of the configured length', async () => {
    let body: unknown;
    server.use(
      http.get('/api/v1/auth/me/kiosk-pin', () => HttpResponse.json({ enabled: true, pin_length: 4, has_pin: false })),
      http.put('/api/v1/auth/me/kiosk-pin', async ({ request }) => { body = await request.json(); return HttpResponse.json({ ok: true }); }),
    );
    wrap(<KioskPinCard />);
    const input = await screen.findByLabelText('New kiosk PIN');
    await waitFor(() => expect(input).toHaveAttribute('maxlength', '4'));
    await userEvent.type(input, '12a34');
    expect(screen.getByRole('button', { name: 'Set PIN' })).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: 'Set PIN' }));
    await waitFor(() => expect(body).toEqual({ pin: '1234' }));
  });

  it('offers removal when a PIN exists', async () => {
    let removed = false;
    server.use(
      http.get('/api/v1/auth/me/kiosk-pin', () => HttpResponse.json({ enabled: false, pin_length: 6, has_pin: true })),
      http.delete('/api/v1/auth/me/kiosk-pin', () => { removed = true; return HttpResponse.json({ ok: true }); }),
    );
    wrap(<KioskPinCard />);
    expect(await screen.findByText(/turned off by an administrator/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Remove PIN' }));
    await waitFor(() => expect(removed).toBe(true));
  });
});
