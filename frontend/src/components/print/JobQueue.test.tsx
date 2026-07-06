import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import JobQueue from './JobQueue';
import type { PrintJob } from '../../types';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

const heldJob: PrintJob = {
  id: 1,
  cups_job_id: null,
  title: 'Doc',
  filename: 'doc.pdf',
  file_size: 1024,
  mime_type: 'application/pdf',
  status: 'held',
  copies: 1,
  duplex: false,
  media: 'A4',
  source_type: 'upload',
  printer_id: null,
  has_pin: false,
  error_message: null,
  created_at: '2026-07-05T00:00:00Z',
  updated_at: '2026-07-05T00:00:00Z',
  completed_at: null,
};

describe('JobQueue', () => {
  it('renders job rows from the jobs query', async () => {
    server.use(
      http.get('/api/jobs', () => HttpResponse.json({ jobs: [heldJob], total: 1 })),
      http.get('/api/printers', () => HttpResponse.json([])),
    );

    const { container } = render(<JobQueue />, { wrapper: makeWrapper() });

    // Loading state is now a row-shaped Skeleton (no text) rather than a
    // "Loading jobs..." string; assert on its shimmer marker instead.
    expect(container.querySelector('.skeleton-shimmer')).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText('doc.pdf')).toBeInTheDocument());
    expect(screen.getByText('Held')).toBeInTheDocument();
    expect(container.querySelector('.skeleton-shimmer')).not.toBeInTheDocument();
  });

  it('release upserts the mutation response into the row without a second list GET', async () => {
    let jobsGetCount = 0;
    let releaseCalled = false;

    server.use(
      http.get('/api/jobs', () => {
        jobsGetCount += 1;
        return HttpResponse.json({ jobs: [heldJob], total: 1 });
      }),
      http.get('/api/printers', () => HttpResponse.json([])),
      http.post('/api/jobs/1/release', () => {
        releaseCalled = true;
        return HttpResponse.json({ ...heldJob, status: 'printing' });
      }),
    );

    const user = userEvent.setup();
    render(<JobQueue />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('doc.pdf')).toBeInTheDocument());
    expect(screen.getByText('Held')).toBeInTheDocument();
    expect(jobsGetCount).toBe(1);

    await user.click(screen.getByRole('button', { name: 'Print' }));

    await waitFor(() => expect(releaseCalled).toBe(true));
    // The row status flips using the API response only — no refetch.
    await waitFor(() => expect(screen.getByText('Printing')).toBeInTheDocument());
    expect(screen.queryByText('Held')).not.toBeInTheDocument();
    expect(jobsGetCount).toBe(1);
  });
});
