import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../test/mocks/server';
import FilesPage from './FilesPage';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

// FilesPage defaults to the Network tab, so NetworkBrowser is what's mounted
// by rendering <FilesPage /> without further interaction.
describe('NetworkBrowser (via FilesPage)', () => {
  it('renders shares, then a share listing from msw when one is opened', async () => {
    server.use(
      http.get('/api/smb/shares', () =>
        HttpResponse.json([
          { id: 1, name: 'Office Share', server: 'nas', share_name: 'docs', username: null, domain: '', created_at: '2026-01-01T00:00:00Z' },
        ]),
      ),
      http.get('/api/smb/browse/1', ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get('path')).toBe('/');
        return HttpResponse.json([
          { name: 'report.pdf', is_directory: false, size: 2048, modified_at: null },
        ]);
      }),
    );

    const user = userEvent.setup();
    render(<FilesPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('Office Share')).toBeInTheDocument());
    expect(screen.queryByText('report.pdf')).not.toBeInTheDocument();

    await user.click(screen.getByText('Office Share'));

    await waitFor(() => expect(screen.getByText('report.pdf')).toBeInTheDocument());
  });
});
