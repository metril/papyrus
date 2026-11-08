import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse, delay } from 'msw';
import { server } from '../../test/mocks/server';
import ScanBacksDialog from './ScanBacksDialog';

function renderDialog(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ScanBacksDialog scanId="abc" onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

describe('ScanBacksDialog', () => {
  it('sends the defaults (blank removal on, no rotation) and closes on success', async () => {
    let body: unknown;
    server.use(
      http.post('/api/scanner/scans/abc/backs', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ scan_id: 'abc', page_count: 4 });
      }),
    );
    const onClose = renderDialog();
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(body).toEqual({ rotate_backs: false, skip_blank_backs: true });
  });

  it('sends the chosen options', async () => {
    let body: unknown;
    server.use(
      http.post('/api/scanner/scans/abc/backs', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ scan_id: 'abc' });
      }),
    );
    renderDialog();
    await userEvent.click(screen.getByLabelText(/short edge/i));
    await userEvent.click(screen.getByLabelText(/remove blank backs/i));
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() => expect(body).toEqual({ rotate_backs: true, skip_blank_backs: false }));
  });

  it('shows the backend refusal and stays open', async () => {
    server.use(
      http.post('/api/scanner/scans/abc/backs', () =>
        HttpResponse.json({ detail: 'No pages scanned from ADF' }, { status: 503 }),
      ),
    );
    const onClose = renderDialog();
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('No pages scanned from ADF');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('locks the form while the long request is running', async () => {
    server.use(
      http.post('/api/scanner/scans/abc/backs', async () => {
        await delay(100);
        return HttpResponse.json({ scan_id: 'abc' });
      }),
    );
    renderDialog();
    await userEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(await screen.findByRole('status')).toHaveTextContent(/minute or more/i);
    expect(screen.getByRole('button', { name: 'Scanning...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  });
});
