import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import TokenOnceDialog from './TokenOnceDialog';
import KiosksCard from './KiosksCard';

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const kiosk = {
  id: 'k1', name: 'Front', token_id: 't1', last_seen_at: null,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};

describe('TokenOnceDialog', () => {
  it('shows the token, copies it and closes', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const onClose = vi.fn();
    render(<TokenOnceDialog token="pprs_abc" kioskName="Front" onClose={onClose} />);
    expect(screen.getByText('pprs_abc')).toBeInTheDocument();
    expect(screen.getByText(/won't be shown again/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith('pprs_abc');
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe('KiosksCard', () => {
  it('lists kiosks with last seen and shows the token once after create', async () => {
    server.use(
      http.get('/api/v1/kiosks', () => HttpResponse.json([{ ...kiosk, last_seen_at: new Date().toISOString() }])),
      http.post('/api/v1/kiosks', () => HttpResponse.json({ kiosk, token: 'pprs_new' }, { status: 201 })),
    );
    render(<KiosksCard />, { wrapper: wrap() });
    expect(await screen.findByText('Front')).toBeInTheDocument();
    expect(screen.getByText(/Last seen: just now/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('New kiosk name'), 'Lobby');
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(await screen.findByText('pprs_new')).toBeInTheDocument();
  });

  it('shows the rotated token once', async () => {
    server.use(
      http.get('/api/v1/kiosks', () => HttpResponse.json([kiosk])),
      http.post('/api/v1/kiosks/k1/rotate-token', () => HttpResponse.json({ kiosk, token: 'pprs_rot' })),
    );
    render(<KiosksCard />, { wrapper: wrap() });
    await userEvent.click(await screen.findByRole('button', { name: 'Rotate token' }));
    expect(await screen.findByText('pprs_rot')).toBeInTheDocument();
  });
});
