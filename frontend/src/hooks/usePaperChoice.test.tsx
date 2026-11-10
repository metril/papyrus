import { describe, it, expect } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse, delay } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../test/mocks/server';
import { usePaperChoice } from './usePaperChoice';

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const ALLOWED = ['A4', 'Letter'];

function paper(media: string | null) {
  server.use(http.get('/api/v1/printers/default/paper', () => HttpResponse.json({ media })));
}

describe('usePaperChoice', () => {
  it('starts at A4 and switches to the detected paper', async () => {
    paper('Letter');
    const { result } = renderHook(() => usePaperChoice(ALLOWED), { wrapper });
    expect(result.current[0]).toBe('A4');
    await waitFor(() => expect(result.current[0]).toBe('Letter'));
  });

  it('keeps A4 when the printer reports nothing', async () => {
    paper(null);
    const { result } = renderHook(() => usePaperChoice(ALLOWED), { wrapper });
    await new Promise((r) => setTimeout(r, 50));
    expect(result.current[0]).toBe('A4');
  });

  it('ignores detected paper the form cannot offer', async () => {
    paper('Legal');
    const { result } = renderHook(() => usePaperChoice(ALLOWED), { wrapper });
    await new Promise((r) => setTimeout(r, 50));
    expect(result.current[0]).toBe('A4');
  });

  it('falls back to A4 when the request fails', async () => {
    server.use(http.get('/api/v1/printers/default/paper', () => new HttpResponse(null, { status: 500 })));
    const { result } = renderHook(() => usePaperChoice(ALLOWED), { wrapper });
    await new Promise((r) => setTimeout(r, 50));
    expect(result.current[0]).toBe('A4');
  });

  it('does not overwrite an explicit choice when the query resolves', async () => {
    server.use(
      http.get('/api/v1/printers/default/paper', async () => {
        await delay(30);
        return HttpResponse.json({ media: 'Letter' });
      }),
    );
    const { result } = renderHook(() => usePaperChoice(['A4', 'Letter', 'Legal']), { wrapper });
    act(() => result.current[1]('Legal'));
    await new Promise((r) => setTimeout(r, 120));
    expect(result.current[0]).toBe('Legal');
  });
});
