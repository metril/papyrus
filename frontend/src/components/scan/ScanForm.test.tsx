import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import ScanForm from './ScanForm';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

/** Minimal WebSocket stand-in: records every url a connection was opened
 * for, without actually attempting a network connection (jsdom has no real
 * WS server to talk to anyway). */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 0;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.onclose?.();
  }

  send() {
    // no-op
  }
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

describe('ScanForm', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    server.use(http.get('/api/scanner/profiles', () => HttpResponse.json([])));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('opens the progress socket on a client-generated id and posts that same id', async () => {
    let postedBody: { scan_id?: string } | null = null;

    server.use(
      http.post('/api/scanner/scan', async ({ request }) => {
        postedBody = (await request.json()) as { scan_id?: string };
        return HttpResponse.json({
          id: 1,
          scan_id: postedBody.scan_id,
          status: 'completed',
          resolution: 300,
          mode: 'Color',
          format: 'pdf',
          source: 'Flatbed',
          page_count: 1,
          file_size: 1234,
          error_message: null,
          created_at: '2026-08-26T00:00:00Z',
          completed_at: '2026-08-26T00:00:00Z',
        });
      }),
    );

    const user = userEvent.setup();
    render(<ScanForm />, { wrapper: makeWrapper() });

    await user.click(screen.getByRole('button', { name: 'Start scan' }));

    await waitFor(() => expect(postedBody).not.toBeNull());
    const sentScanId = postedBody!.scan_id;
    expect(sentScanId).toMatch(UUID_RE);

    // A progress socket was opened for exactly this id -- before F26 the
    // socket url derived from the *response*, so client and channel could
    // never be guaranteed to match (and every frame arrived before any
    // subscriber existed at all, since the POST doesn't resolve until the
    // scan is done).
    const wsForThisScan = FakeWebSocket.instances.find((ws) =>
      ws.url.endsWith(`/api/scanner/ws/scan/${sentScanId}`),
    );
    expect(wsForThisScan).toBeDefined();

    await waitFor(() => expect(screen.getByText('Scan completed!')).toBeInTheDocument());
  });

  it('generates a different id for each scan', async () => {
    const postedIds: string[] = [];

    server.use(
      http.post('/api/scanner/scan', async ({ request }) => {
        const body = (await request.json()) as { scan_id?: string };
        postedIds.push(body.scan_id!);
        return HttpResponse.json({
          id: postedIds.length,
          scan_id: body.scan_id,
          status: 'completed',
          resolution: 300,
          mode: 'Color',
          format: 'pdf',
          source: 'Flatbed',
          page_count: 1,
          file_size: 1234,
          error_message: null,
          created_at: '2026-08-26T00:00:00Z',
          completed_at: '2026-08-26T00:00:00Z',
        });
      }),
    );

    const user = userEvent.setup();
    render(<ScanForm />, { wrapper: makeWrapper() });

    await user.click(screen.getByRole('button', { name: 'Start scan' }));
    await waitFor(() => expect(postedIds).toHaveLength(1));

    await user.click(screen.getByRole('button', { name: 'Start scan' }));
    await waitFor(() => expect(postedIds).toHaveLength(2));

    expect(postedIds[0]).not.toBe(postedIds[1]);
  });

  describe('feeder format restriction', () => {
    const base = {
      resolutions: [100, 300],
      modes: ['Color'],
      formats: ['png', 'jpeg', 'tiff', 'pdf'],
      sources: ['Flatbed', 'ADF'],
    };

    async function renderWith(feeder_formats: string[]) {
      server.use(
        http.get('/api/scanner/options', () => HttpResponse.json({ ...base, feeder_formats })),
      );
      const user = userEvent.setup();
      render(<ScanForm />, { wrapper: makeWrapper() });
      await screen.findByRole('option', { name: '100 DPI' });
      return user;
    }

    it('offers only PDF for the feeder when the device requires it, and says why', async () => {
      const user = await renderWith(['pdf']);
      await user.selectOptions(screen.getByDisplayValue('Flatbed'), 'ADF');
      expect(screen.queryByRole('option', { name: 'JPEG' })).not.toBeInTheDocument();
      expect(screen.getByText(/saves as PDF only/)).toBeInTheDocument();
    });

    it('sends pdf even if a profile or earlier choice was jpeg', async () => {
      let body: { format?: string } = {};
      server.use(
        http.post('/api/scanner/scan', async ({ request }) => {
          body = (await request.json()) as { format?: string };
          return HttpResponse.json({ id: 1, scan_id: 'x', status: 'completed' }, { status: 201 });
        }),
      );
      const user = await renderWith(['pdf']);
      await user.selectOptions(screen.getByDisplayValue('PDF'), 'jpeg');
      await user.selectOptions(screen.getByDisplayValue('Flatbed'), 'ADF');
      await user.click(screen.getByRole('button', { name: 'Start scan' }));
      await waitFor(() => expect(body.format).toBe('pdf'));
    });

    it('leaves every format available when the feeder has no restriction', async () => {
      const user = await renderWith(['png', 'jpeg', 'tiff', 'pdf']);
      await user.selectOptions(screen.getByDisplayValue('Flatbed'), 'ADF');
      expect(screen.getByRole('option', { name: 'JPEG' })).toBeInTheDocument();
      expect(screen.queryByText(/saves as PDF only/)).not.toBeInTheDocument();
    });
  });
});
