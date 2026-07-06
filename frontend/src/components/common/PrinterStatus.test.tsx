import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import PrinterStatus from './PrinterStatus';
import { printerStatusRefetchInterval } from '../../api/queries';
import { useConnectionStore } from '../../store/connectionStore';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('printerStatusRefetchInterval', () => {
  it('falls back to a 180s poll while the printers socket is down', () => {
    expect(printerStatusRefetchInterval(false)).toBe(180_000);
  });

  it('never polls while the printers socket is connected', () => {
    expect(printerStatusRefetchInterval(true)).toBe(false);
  });
});

describe('PrinterStatus', () => {
  it('renders the CUPS status blob (state + markers) from usePrinterStatus', async () => {
    // With the printers socket down the query would poll on the 180s interval;
    // the render itself is driven by the single initial fetch below.
    useConnectionStore.setState({ printersConnected: false });

    server.use(
      http.get('/api/printer/status', () =>
        HttpResponse.json({
          state: 3,
          state_message: 'Ready',
          accepting_jobs: true,
          markers: [{ name: 'Black Toner', level: 42, color: 'black' }],
          state_reasons: ['none'],
        }),
      ),
    );

    render(<PrinterStatus />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('Idle')).toBeInTheDocument());
    expect(screen.getByText('Black Toner')).toBeInTheDocument();
    expect(screen.getByText('42%')).toBeInTheDocument();
  });
});
