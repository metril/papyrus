import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse, delay } from 'msw';
import { server } from '../../test/mocks/server';
import ScanBacksDialog from './ScanBacksDialog';

function renderDialog(onClose = vi.fn(), onMerged = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ScanBacksDialog scanId="abc" onClose={onClose} onMerged={onMerged} />
    </QueryClientProvider>,
  );
  return { onClose, onMerged };
}

describe('ScanBacksDialog', () => {
  const ok = (blanks = 0) =>
    HttpResponse.json({
      merged: { scan_id: 'm', page_count: 6 },
      backs: { scan_id: 'b' },
      fronts: { scan_id: 'abc' },
      blanks_dropped: blanks,
    });

  it('sends the defaults (blank removal off, no rotation) and reports the merge', async () => {
    let body: unknown;
    server.use(
      http.post('/api/v1/scans/abc/backs', async ({ request }) => {
        body = await request.json();
        return ok();
      }),
    );
    const { onClose, onMerged } = renderDialog();
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(onMerged).toHaveBeenCalled());
    expect(body).toEqual({ rotate_backs: false, skip_blank_backs: false });
    expect(screen.getByRole('status')).toHaveTextContent(/6 pages/);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Start' })).toBeNull();
  });

  it('says how many blank backs were removed', async () => {
    server.use(http.post('/api/v1/scans/abc/backs', () => ok(2)));
    renderDialog();
    await userEvent.click(screen.getByLabelText(/remove blank backs/i));
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByRole('status')).toHaveTextContent(/2 blank backs removed/);
  });

  it('sends the chosen options', async () => {
    let body: unknown;
    server.use(
      http.post('/api/v1/scans/abc/backs', async ({ request }) => {
        body = await request.json();
        return ok();
      }),
    );
    renderDialog();
    await userEvent.click(screen.getByLabelText(/short-edge flip/i));
    await userEvent.click(screen.getByLabelText(/remove blank backs/i));
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(body).toEqual({ rotate_backs: true, skip_blank_backs: true }));
  });

  it('shows the backend refusal and stays open', async () => {
    server.use(
      http.post('/api/v1/scans/abc/backs', () =>
        HttpResponse.json({ detail: 'No pages scanned from ADF' }, { status: 503 }),
      ),
    );
    const { onClose } = renderDialog();
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('No pages scanned from ADF');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('locks the form while the long request is running', async () => {
    server.use(
      http.post('/api/v1/scans/abc/backs', async () => {
        await delay(100);
        return ok();
      }),
    );
    renderDialog();
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByRole('status')).toHaveTextContent(/minute or more/i);
    expect(screen.getByRole('button', { name: 'Scanning...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(await screen.findByText(/added a merged scan/i)).toBeInTheDocument();
  });
});
