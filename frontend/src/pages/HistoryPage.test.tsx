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
  user_id: null,
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

    const { container } = render(<HistoryPage />, { wrapper: makeWrapper() });

    // Loading state is now a row-shaped Skeleton (no text) rather than a
    // "Loading history..." string; assert on its shimmer marker instead.
    expect(container.querySelector('.skeleton-shimmer')).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText('doc.pdf')).toBeInTheDocument());
    expect(screen.getByText('PDF 300 DPI')).toBeInTheDocument();
    expect(container.querySelector('.skeleton-shimmer')).not.toBeInTheDocument();
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

    await user.click(screen.getByRole('button', { name: 'Delete selected (2)' }));

    await waitFor(() => expect(jobsPostBody).toEqual({ ids: [1] }));
    expect(scansPostBody).toEqual({ scan_ids: ['scan-abc'] });

    await waitFor(() =>
      expect(screen.getByText('Nothing here yet')).toBeInTheDocument()
    );
    expect(screen.queryByText('doc.pdf')).not.toBeInTheDocument();
    expect(screen.queryByText('PDF 300 DPI')).not.toBeInTheDocument();
  });

  it('deleting a selected row prunes it from the selection instead of leaving a phantom count (F147)', async () => {
    let jobDeleted = false;
    server.use(
      http.get('/api/jobs', () =>
        HttpResponse.json(jobDeleted ? { jobs: [], total: 0 } : { jobs: [job], total: 1 }),
      ),
      http.get('/api/scanner/scans', () => HttpResponse.json({ scans: [scan], total: 1 })),
      http.delete('/api/jobs/1', () => {
        jobDeleted = true;
        return HttpResponse.json({});
      }),
    );

    const user = userEvent.setup();
    render(<HistoryPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('doc.pdf')).toBeInTheDocument());

    // Select just the print row (index 0 is the "select all" header
    // checkbox; index 1 is doc.pdf, sorted newest-first ahead of the scan).
    const checkboxes = screen.getAllByRole('checkbox');
    await user.click(checkboxes[1]);
    expect(screen.getByText('1 selected')).toBeInTheDocument();

    // That row's own Delete button (not "Delete selected (1)").
    await user.click(screen.getAllByRole('button', { name: 'Delete' })[0]);

    await waitFor(() => expect(screen.queryByText('doc.pdf')).not.toBeInTheDocument());
    // The bar must clear itself — not keep reading "1 selected" for a row
    // that's already gone (which also used to make "Delete selected"
    // silently issue no request, since the id no longer resolves).
    expect(screen.queryByText('1 selected')).not.toBeInTheDocument();
  });

  it('"Load more" fetches and appends the next page (F81)', async () => {
    const page0Job: PrintJob = { ...job, id: 1, filename: 'newest.pdf' };
    const page1Job: PrintJob = { ...job, id: 2, filename: 'oldest.pdf', created_at: '2026-06-01T00:00:00Z' };
    const jobsCalls: string[] = [];

    server.use(
      http.get('/api/jobs', ({ request }) => {
        const offset = new URL(request.url).searchParams.get('offset') ?? '0';
        jobsCalls.push(offset);
        return HttpResponse.json(
          offset === '0' ? { jobs: [page0Job], total: 2 } : { jobs: [page1Job], total: 2 },
        );
      }),
      http.get('/api/scanner/scans', () => HttpResponse.json({ scans: [], total: 0 })),
    );

    const user = userEvent.setup();
    render(<HistoryPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('newest.pdf')).toBeInTheDocument());
    expect(screen.queryByText('oldest.pdf')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Load more' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Load more' }));

    await waitFor(() => expect(screen.getByText('oldest.pdf')).toBeInTheDocument());
    // Page 0 stays rendered too — "Load more" appends, it doesn't replace.
    expect(screen.getByText('newest.pdf')).toBeInTheDocument();
    expect(jobsCalls).toContain('50');
    // Both pages' items are now loaded (2 of 2) — no more to fetch.
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
  });
});
