import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import ScanList from './ScanList';
import type { ScanJob } from '../../types';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

const scan: ScanJob = {
  id: 1,
  scan_id: 'scan-abc',
  status: 'completed',
  resolution: 300,
  mode: 'Color',
  format: 'pdf',
  source: 'Flatbed',
  page_count: 1,
  file_size: 2048,
  error_message: null,
  created_at: '2026-07-04T00:00:00Z',
  completed_at: '2026-07-04T00:00:00Z',
};

describe('ScanList', () => {
  it('deletes a scan, removing its row from the cache without a refetch', async () => {
    let scansGetCount = 0;
    let deleteCalled = false;

    server.use(
      http.get('/api/scanner/scans', () => {
        scansGetCount += 1;
        return HttpResponse.json({ scans: [scan], total: 1 });
      }),
      http.delete('/api/scanner/scans/scan-abc', () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const user = userEvent.setup();
    render(<ScanList />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText(/300 DPI/)).toBeInTheDocument());
    expect(scansGetCount).toBe(1);

    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(deleteCalled).toBe(true));
    await waitFor(() => expect(screen.getByText('No scans yet.')).toBeInTheDocument());
    expect(screen.queryByText(/300 DPI/)).not.toBeInTheDocument();
    // Removal is a cache update from the delete, not a refetch.
    expect(scansGetCount).toBe(1);
  });
});
