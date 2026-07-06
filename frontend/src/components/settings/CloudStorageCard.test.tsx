import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import CloudStorageCard from './CloudStorageCard';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('CloudStorageCard', () => {
  it('disconnects a provider: DELETE fires, the list refetches, and the row disappears', async () => {
    let getCount = 0;
    server.use(
      http.get('/api/cloud/providers', () => {
        getCount += 1;
        return HttpResponse.json({
          providers:
            getCount === 1
              ? [{ id: 1, provider: 'gdrive', connected_at: '2026-01-01T00:00:00Z' }]
              : [],
        });
      }),
      http.delete('/api/cloud/disconnect/1', () => new HttpResponse(null, { status: 204 })),
    );

    const user = userEvent.setup();
    render(<CloudStorageCard />, { wrapper: makeWrapper() });

    // The card is collapsible and starts closed — open it first.
    await user.click(screen.getByText('Cloud Storage'));

    await waitFor(() => expect(screen.getByText('Google Drive')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'Disconnect' }));

    await waitFor(() => expect(getCount).toBe(2));
    await waitFor(() => expect(screen.queryByText('Google Drive')).not.toBeInTheDocument());
  });
});
