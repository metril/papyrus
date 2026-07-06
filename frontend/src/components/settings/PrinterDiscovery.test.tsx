import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import PrinterDiscovery from './PrinterDiscovery';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('PrinterDiscovery', () => {
  it('renders a discovered device once the scan resolves', async () => {
    server.use(
      http.get('/api/printers/discover', () =>
        HttpResponse.json({
          printers: [
            {
              name: 'Brother DCP-L2540DW',
              ip: '10.0.0.5',
              port: 631,
              make_model: 'Brother DCP-L2540DW',
              location: null,
              uri: 'ipp://10.0.0.5/ipp/print',
              uuid: 'abc-123',
              protocols: ['ipp'],
              already_configured: false,
            },
          ],
        }),
      ),
    );

    render(<PrinterDiscovery onSelect={vi.fn()} />, { wrapper: makeWrapper() });

    expect(screen.getByText('Scanning network…')).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText('Brother DCP-L2540DW')).toBeInTheDocument());
    expect(screen.getByText('1 device found')).toBeInTheDocument();
    expect(screen.queryByText(/Scan failed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No printers found/)).not.toBeInTheDocument();
  });

  it('shows the error state — not the empty-state copy — when discovery fails', async () => {
    server.use(
      http.get('/api/printers/discover', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );

    render(<PrinterDiscovery onSelect={vi.fn()} />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('Scan failed — try again.')).toBeInTheDocument());
    expect(screen.queryByText(/No printers found/)).not.toBeInTheDocument();
  });
});
