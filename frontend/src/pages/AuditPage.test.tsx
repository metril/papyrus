import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../test/mocks/server';
import AuditPage from './AuditPage';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function entry(overrides: Partial<{ id: number; action: string; entity_id: string | null }>) {
  return {
    id: 1,
    action: 'print.release',
    entity_type: 'print_job',
    entity_id: null,
    user_id: null,
    source: 'web',
    ip_address: '10.0.0.5',
    detail: null,
    created_at: '2026-07-05T12:00:00Z',
    ...overrides,
  };
}

describe('AuditPage', () => {
  it('renders page 1 entries from the msw-mocked audit log', async () => {
    server.use(
      http.get('/api/admin/audit', ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get('limit')).toBe('50');
        expect(url.searchParams.get('offset')).toBe('0');
        return HttpResponse.json({
          entries: [entry({ id: 1, action: 'print.release', entity_id: '7' })],
          total: 1,
        });
      }),
    );

    const { container } = render(<AuditPage />, { wrapper: makeWrapper() });

    expect(container.querySelector('.skeleton-shimmer')).toBeInTheDocument();

    // Match on the entity id, not the action name — the action filter
    // dropdown always renders an option with the same text as the action
    // ("print.release"), so it's not a reliable "the row rendered" signal.
    await waitFor(() => expect(screen.getByText('#7')).toBeInTheDocument());
    expect(screen.getByText('1 entries')).toBeInTheDocument();
  });

  it('keeps page 1 rows visible (no empty flash) while page 2 is still loading', async () => {
    // Page 2's response is gated behind this promise so the test can assert
    // on in-between UI state before it resolves.
    let resolvePage2: () => void = () => {};
    const page2Gate = new Promise<void>((resolve) => { resolvePage2 = resolve; });

    server.use(
      http.get('/api/admin/audit', async ({ request }) => {
        const url = new URL(request.url);
        const offset = Number(url.searchParams.get('offset'));
        if (offset === 0) {
          return HttpResponse.json({
            entries: [entry({ id: 1, action: 'print.release', entity_id: '101' })],
            total: 60,
          });
        }
        await page2Gate;
        return HttpResponse.json({
          entries: [entry({ id: 2, action: 'scan.complete', entity_id: '202' })],
          total: 60,
        });
      }),
    );

    const user = userEvent.setup();
    render(<AuditPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('#101')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'Next' }));

    // placeholderData: keepPreviousData means page 1's row is still rendered
    // immediately after clicking Next, rather than the list going empty while
    // page 2 fetches.
    expect(screen.getByText('#101')).toBeInTheDocument();
    expect(screen.queryByText('#202')).not.toBeInTheDocument();

    resolvePage2();

    await waitFor(() => expect(screen.getByText('#202')).toBeInTheDocument());
    expect(screen.queryByText('#101')).not.toBeInTheDocument();
  });

  it('shows "Admin access required" when the audit log request 403s', async () => {
    server.use(
      http.get('/api/admin/audit', () =>
        HttpResponse.json({ detail: 'Forbidden' }, { status: 403 }),
      ),
    );

    render(<AuditPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('Admin access required')).toBeInTheDocument());
  });
});
