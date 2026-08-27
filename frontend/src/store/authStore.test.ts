import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../test/mocks/server';
import { useAuthStore } from './authStore';
import { queryClient } from '../api/queryClient';
import { queryKeys } from '../api/queries';
import type { User } from '../types';

const user: User = { id: 'u1', email: 'a@example.com', display_name: 'Alice', role: 'user' };

describe('authStore', () => {
  beforeEach(() => {
    useAuthStore.setState({ user, loading: false, error: null });
    queryClient.setQueryData(queryKeys.cloudProviders, [{ id: 1, provider: 'gdrive', connected_at: '2026-01-01' }]);
  });

  it('signOut clears the user and the Query cache (F16 / F83)', () => {
    useAuthStore.getState().signOut();

    expect(useAuthStore.getState().user).toBeNull();
    expect(queryClient.getQueryData(queryKeys.cloudProviders)).toBeUndefined();
  });

  it('logout posts to /auth/logout, then signs out and clears the cache', async () => {
    let called = false;
    server.use(
      http.post('/api/auth/logout', () => {
        called = true;
        return HttpResponse.json({});
      }),
    );

    await useAuthStore.getState().logout();

    expect(called).toBe(true);
    expect(useAuthStore.getState().user).toBeNull();
    expect(queryClient.getQueryData(queryKeys.cloudProviders)).toBeUndefined();
  });

  it('logout signs out and clears the cache even when the POST fails (F83)', async () => {
    server.use(
      http.post('/api/auth/logout', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })),
    );

    // Must not throw / produce an unhandled rejection.
    await expect(useAuthStore.getState().logout()).resolves.toBeUndefined();

    expect(useAuthStore.getState().user).toBeNull();
    expect(queryClient.getQueryData(queryKeys.cloudProviders)).toBeUndefined();
  });
});
