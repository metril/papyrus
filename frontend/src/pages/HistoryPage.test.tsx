import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../test/mocks/server';
import HistoryPage from './HistoryPage';
import type { PrintJob, ScanJob } from '../types';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

const job: PrintJob = {
  id: 1,
  cups_job_id: null,
  title: 'Doc',
  filename: 'doc.pdf',
  file_size: 1024,
  mime_type: 'application/pdf',
  status: 'completed',
  copies: 1,
  duplex: false,
  media: 'A4',
  source_type: 'upload',
  printer_id: null,
  has_pin: false,
  error_message: null,
  created_at: '2026-07-05T00:00:00Z',
  updated_at: '2026-07-05T00:00:00Z',
  completed_at: '2026-07-05T00:05:00Z',
};

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

describe('HistoryPage', () => {
  it('renders a unified list from the jobs and scans queries', async () => {
    server.use(
      http.get('/api/jobs', () => HttpResponse.json({ jobs: [job], total: 1 })),
      http.get('/api/scanner/scans', () => HttpResponse.json({ scans: [scan], total: 1 })),
    );

    render(<HistoryPage />, { wrapper: makeWrapper() });

    expect(screen.getByText('Loading history...')).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText('doc.pdf')).toBeInTheDocument());
    expect(screen.getByText('PDF 300 DPI')).toBeInTheDocument();
    expect(screen.queryByText('Loading history...')).not.toBeInTheDocument();
  });

  it('bulk-deletes selected rows: fires both bulk-delete POSTs and refetches to empty', async () => {
    let jobsGetCount = 0;
    let scansGetCount = 0;
    let jobsPostBody: unknown = null;
    let scansPostBody: unknown = null;

    server.use(
      http.get('/api/jobs', () => {
        jobsGetCount += 1;
        return HttpResponse.json(
          jobsGetCount === 1 ? { jobs: [job], total: 1 } : { jobs: [], total: 0 },
        );
      }),
      http.get('/api/scanner/scans', () => {
        scansGetCount += 1;
        return HttpResponse.json(
          scansGetCount === 1 ? { scans: [scan], total: 1 } : { scans: [], total: 0 },
        );
      }),
      http.post('/api/jobs/bulk-delete', async ({ request }) => {
        jobsPostBody = await request.json();
        return HttpResponse.json({});
      }),
      http.post('/api/scanner/scans/bulk-delete', async ({ request }) => {
        scansPostBody = await request.json();
        return HttpResponse.json({});
      }),
    );

    const user = userEvent.setup();
    render(<HistoryPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('doc.pdf')).toBeInTheDocument());
    expect(screen.getByText('PDF 300 DPI')).toBeInTheDocument();

    // Select-all header checkbox selects both rows.
    const checkboxes = screen.getAllByRole('checkbox');
    await user.click(checkboxes[0]);

    await user.click(screen.getByRole('button', { name: 'Delete Selected (2)' }));

    await waitFor(() => expect(jobsPostBody).toEqual({ ids: [1] }));
    expect(scansPostBody).toEqual({ scan_ids: ['scan-abc'] });

    await waitFor(() =>
      expect(screen.getByText('No items match your filters.')).toBeInTheDocument()
    );
    expect(screen.queryByText('doc.pdf')).not.toBeInTheDocument();
    expect(screen.queryByText('PDF 300 DPI')).not.toBeInTheDocument();
  });
});
