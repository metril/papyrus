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
 *  - the error is an axios 401 (the client interceptor already redirects).
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

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
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
