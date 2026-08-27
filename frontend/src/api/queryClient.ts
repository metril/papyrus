import { QueryClient, QueryCache, MutationCache } from '@tanstack/react-query';
import axios from 'axios';
import { showToast } from '../store/toastStore';

interface ApiErrorResponse {
  detail?: string;
}

/**
 * Global error handler shared by the query and mutation caches. Toasts the
 * server's `detail` (falling back to the error message), but stays silent when:
 *  - the caller opted out via `meta.suppressGlobalError === true`, or
 *  - the error is an axios 401 (the client interceptor already signs the
 *    user out and unmounts this page in favor of LoginScreen — see F16).
 */
function reportError(error: unknown, suppressGlobalError: unknown): void {
  if (suppressGlobalError === true) return;

  if (axios.isAxiosError<ApiErrorResponse>(error)) {
    if (error.response?.status === 401) return;
    showToast(error.response?.data?.detail ?? error.message);
    return;
  }

  showToast(error instanceof Error ? error.message : String(error));
}

/**
 * F143: a bare `retry: 1` retries every failure once, including deterministic
 * 4xx responses (a non-admin's 403 on /settings, an expired session's 401) —
 * pointless extra round trips that also double up client.ts's redirect-side
 * effects. Only retry once for something that might succeed on a second try
 * (network errors, 5xx); anything the server has already definitively
 * rejected (status < 500) fails immediately.
 */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false;
  if (axios.isAxiosError(error)) {
    const status = error.response?.status;
    if (status !== undefined && status < 500) return false;
  }
  return true;
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: shouldRetry,
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => reportError(error, query.meta?.suppressGlobalError),
  }),
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) =>
      reportError(error, mutation.meta?.suppressGlobalError),
  }),
});
