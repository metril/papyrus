import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../test/mocks/server';
import CopyPage from './CopyPage';

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CopyPage />
    </QueryClientProvider>,
  );
}

describe('CopyPage paper default', () => {
  it('defaults to the printer paper and sends it', async () => {
    server.use(http.get('/api/v1/printers/default/paper', () => HttpResponse.json({ media: 'Letter' })));
    let body: { media?: string } = {};
    server.use(
      http.post('/api/v1/copy', async ({ request }) => {
        body = (await request.json()) as { media?: string };
        return HttpResponse.json({ message: 'ok', scan_id: 's', cups_job_id: 1 });
      }),
    );
    renderPage();
    const select = screen.getByRole('option', { name: 'Letter' }).closest('select')!;
    await waitFor(() => expect(select.value).toBe('Letter'));
    await userEvent.click(screen.getByRole('button', { name: /start copying/i }));
    await waitFor(() => expect(body.media).toBe('Letter'));
  });
});
