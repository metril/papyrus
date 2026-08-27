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

// F82: a connected webdav provider used to fall through the gdrive/dropbox/
// onedrive dispatch and hit GET /cloud/files/{id}, which 400s "Unknown
// provider" — surfaced as "Failed to browse cloud storage" for a provider
// that's actually reachable. It should route to /webdav/{id}/files instead.
describe('CloudBrowser webdav dispatch (via FilesPage)', () => {
  it('browses a webdav provider via /api/webdav/{id}/files, not /api/cloud/files/{id}, and labels it correctly', async () => {
    let cloudFilesHit = false;
    server.use(
      http.get('/api/cloud/providers', () =>
        HttpResponse.json({
          providers: [{ id: 3, provider: 'webdav', connected_at: '2026-01-03T00:00:00Z' }],
        }),
      ),
      http.get('/api/cloud/files/3', () => {
        cloudFilesHit = true;
        return HttpResponse.json({ detail: 'Unknown provider' }, { status: 400 });
      }),
      http.get('/api/webdav/3/files', ({ request }) => {
        expect(new URL(request.url).searchParams.get('path')).toBe('/');
        return HttpResponse.json([
          { name: 'notes.txt', path: '/notes.txt', is_directory: false, size: 12, modified_at: null, mime_type: 'text/plain' },
        ]);
      }),
    );

    const user = userEvent.setup();
    render(<FilesPage />, { wrapper: makeWrapper() });

    await user.click(screen.getByText('Cloud'));
    await waitFor(() => expect(screen.getByText('WebDAV / Nextcloud')).toBeInTheDocument());

    await user.click(screen.getByText('WebDAV / Nextcloud'));

    await waitFor(() => expect(screen.getByText('notes.txt')).toBeInTheDocument());
    expect(cloudFilesHit).toBe(false);
    // No download/print endpoint exists for webdav files — the actions are
    // hidden rather than offered and 404ing.
    expect(screen.queryByRole('button', { name: /print/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /view/i })).not.toBeInTheDocument();
  });
});
