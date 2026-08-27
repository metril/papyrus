import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../test/mocks/server';
import { useToastStore } from '../store/toastStore';
import PrintPage from './PrintPage';

function makeWrapper(initialEntry: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialEntry]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

describe('PrintPage share_failed toast (F133)', () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
    server.use(http.get('/api/printers', () => HttpResponse.json([])));
  });

  it('toasts a pluralized message exactly once and clears the query param when share_failed is present', async () => {
    render(<PrintPage />, { wrapper: makeWrapper('/print?share_failed=2') });

    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
        '2 shared files could not be added to the print queue.',
      ),
    );
    // Removing the query param re-runs the effect; it must not re-toast.
    expect(useToastStore.getState().toasts).toHaveLength(1);
  });

  it('uses the singular form for exactly one failed file', async () => {
    render(<PrintPage />, { wrapper: makeWrapper('/print?share_failed=1') });

    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
        '1 shared file could not be added to the print queue.',
      ),
    );
  });

  it('does not toast when share_failed is absent', async () => {
    render(<PrintPage />, { wrapper: makeWrapper('/print') });

    await waitFor(() => expect(screen.getByText('Print Queue')).toBeInTheDocument());
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});
