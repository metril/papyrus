import { describe, it, expect, beforeEach } from 'vitest';
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

const pinJob: PrintJob = {
  id: 2,
  user_id: null,
  cups_job_id: null,
  title: 'Doc',
  filename: 'secret.pdf',
  file_size: 1024,
  mime_type: 'application/pdf',
  status: 'held',
  copies: 1,
  duplex: false,
  media: 'A4',
  source_type: 'upload',
  printer_id: null,
  has_pin: true,
  error_message: null,
  created_at: '2026-07-05T00:00:00Z',
  updated_at: '2026-07-05T00:00:00Z',
  completed_at: null,
};

const heldJob: PrintJob = {
  id: 1,
  user_id: null,
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

describe('PIN release dialog (F148)', () => {
  async function openPinDialog() {
    const user = userEvent.setup();
    render(<JobQueue />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText('secret.pdf')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Print' }));
    await screen.findByLabelText('Release PIN');
    return user;
  }

  beforeEach(() => {
    server.use(
      http.get('/api/jobs', () => HttpResponse.json({ jobs: [pinJob], total: 1 })),
      http.get('/api/printers', () => HttpResponse.json([])),
    );
  });

  it('maps a 403 to "Invalid PIN"', async () => {
    server.use(http.post('/api/jobs/2/release', () => HttpResponse.json({ detail: 'nope' }, { status: 403 })));
    const user = await openPinDialog();

    await user.type(screen.getByLabelText('Release PIN'), '1234');
    await user.click(screen.getByRole('button', { name: 'Release' }));

    await waitFor(() => expect(screen.getByText('Invalid PIN')).toBeInTheDocument());
  });

  it('maps a 429 to "Too many attempts" — not "Invalid PIN" — so a correct PIN is not mistaken for a wrong one', async () => {
    server.use(
      http.post('/api/jobs/2/release', () =>
        HttpResponse.json({ detail: 'Too many attempts' }, { status: 429 }),
      ),
    );
    const user = await openPinDialog();

    await user.type(screen.getByLabelText('Release PIN'), '1234');
    await user.click(screen.getByRole('button', { name: 'Release' }));

    await waitFor(() => expect(screen.getByText('Too many attempts')).toBeInTheDocument());
    expect(screen.queryByText('Invalid PIN')).not.toBeInTheDocument();
  });

  it('surfaces the server detail for an unrelated failure (e.g. printer offline) instead of "Invalid PIN"', async () => {
    server.use(
      http.post('/api/jobs/2/release', () =>
        HttpResponse.json({ detail: 'Printer is offline' }, { status: 503 }),
      ),
    );
    const user = await openPinDialog();

    await user.type(screen.getByLabelText('Release PIN'), '1234');
    await user.click(screen.getByRole('button', { name: 'Release' }));

    await waitFor(() => expect(screen.getByText('Printer is offline')).toBeInTheDocument());
    expect(screen.queryByText('Invalid PIN')).not.toBeInTheDocument();
  });

  it('closes on Escape', async () => {
    const user = await openPinDialog();
    expect(screen.getByLabelText('Release PIN')).toBeInTheDocument();

    await user.keyboard('{Escape}');

    expect(screen.queryByLabelText('Release PIN')).not.toBeInTheDocument();
  });
});
