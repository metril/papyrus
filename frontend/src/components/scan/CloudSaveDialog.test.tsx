import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import CloudSaveDialog from './CloudSaveDialog';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('CloudSaveDialog (F149)', () => {
  it('fetches providers via the Query cache (useCloudProviders) and labels every provider kind correctly', async () => {
    let providerGetCount = 0;
    server.use(
      http.get('/api/cloud/providers', () => {
        providerGetCount += 1;
        return HttpResponse.json({
          providers: [
            { id: 1, provider: 'gdrive', connected_at: '2026-01-01T00:00:00Z' },
            { id: 2, provider: 'onedrive', connected_at: '2026-01-02T00:00:00Z' },
            { id: 3, provider: 'webdav', connected_at: '2026-01-03T00:00:00Z' },
          ],
        });
      }),
    );

    render(<CloudSaveDialog scanId="scan-1" onClose={() => {}} />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('Google Drive')).toBeInTheDocument());
    // Previously fell back to the raw provider key for anything beyond
    // gdrive/dropbox — onedrive and webdav rendered as literal "onedrive"/
    // "webdav" instead of a real label.
    expect(screen.getByText('OneDrive')).toBeInTheDocument();
    expect(screen.getByText('WebDAV / Nextcloud')).toBeInTheDocument();
    expect(providerGetCount).toBe(1);
  });

  it('saves a gdrive/dropbox/onedrive provider via /scanner/scans/{id}/cloud', async () => {
    let providerId: string | null = null;
    server.use(
      http.get('/api/cloud/providers', () =>
        HttpResponse.json({ providers: [{ id: 1, provider: 'gdrive', connected_at: '2026-01-01T00:00:00Z' }] }),
      ),
      http.post('/api/scanner/scans/scan-1/cloud', ({ request }) => {
        providerId = new URL(request.url).searchParams.get('provider_id');
        return HttpResponse.json({ message: 'ok' });
      }),
    );

    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CloudSaveDialog scanId="scan-1" onClose={onClose} />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('Google Drive')).toBeInTheDocument());
    await user.click(screen.getByText('Google Drive'));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(providerId).toBe('1');
  });

  it('saves a webdav provider via /webdav/{id}/upload, not /scanner/scans/{id}/cloud', async () => {
    let webdavPostBody: unknown = null;
    let cloudEndpointHit = false;
    server.use(
      http.get('/api/cloud/providers', () =>
        HttpResponse.json({ providers: [{ id: 3, provider: 'webdav', connected_at: '2026-01-03T00:00:00Z' }] }),
      ),
      http.post('/api/webdav/3/upload', async ({ request }) => {
        webdavPostBody = await request.json();
        return HttpResponse.json({ message: 'Uploaded' });
      }),
      http.post('/api/scanner/scans/scan-1/cloud', () => {
        cloudEndpointHit = true;
        return HttpResponse.json({ message: 'ok' });
      }),
    );

    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CloudSaveDialog scanId="scan-1" onClose={onClose} />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('WebDAV / Nextcloud')).toBeInTheDocument());
    await user.click(screen.getByText('WebDAV / Nextcloud'));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(webdavPostBody).toEqual({ scan_id: 'scan-1', destination_folder: '/' });
    expect(cloudEndpointHit).toBe(false);
  });
});
