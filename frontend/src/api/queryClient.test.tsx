import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, useQuery } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { queryClient } from './queryClient';
import { useToastStore } from '../store/toastStore';
import { server } from '../test/mocks/server';
import api from './client';

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function useError(meta?: Record<string, unknown>) {
  return useQuery({
    queryKey: ['test', 'error', meta ? 'suppressed' : 'default'],
    queryFn: async () => {
      const { data } = await api.get('/test-error');
      return data;
    },
    retry: false,
    meta,
  });
}

describe('queryClient global error handling', () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
    queryClient.clear();
    server.use(
      http.get('/api/test-error', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );
  });

  it('toasts exactly once with the server detail when a query 500s', async () => {
    const { result } = renderHook(() => useError(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));

    const { toasts } = useToastStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0].message).toBe('boom');
  });

  it('does not toast when meta.suppressGlobalError is true', async () => {
    const { result } = renderHook(() => useError({ suppressGlobalError: true }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});
