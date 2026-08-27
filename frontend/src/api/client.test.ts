import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '../test/mocks/server';
import api from './client';
import { useAuthStore } from '../store/authStore';
import type { User } from '../types';

const user: User = { id: 'u1', email: 'a@example.com', display_name: 'Alice', role: 'user' };

describe('api client 401 interceptor (F16)', () => {
  beforeEach(() => {
    useAuthStore.setState({ user, loading: false, error: null });
  });

  it('signs out locally on a 401 from a non-auth endpoint', async () => {
    server.use(http.get('/api/jobs', () => HttpResponse.json({ detail: 'nope' }, { status: 401 })));

    await expect(api.get('/jobs')).rejects.toBeTruthy();

    expect(useAuthStore.getState().user).toBeNull();
  });

  it.each(['/auth/me', '/auth/local-login', '/auth/providers'])(
    'does not sign out on a 401 from %s',
    async (path) => {
      server.use(http.get(`/api${path}`, () => HttpResponse.json({ detail: 'nope' }, { status: 401 })));

      await expect(api.get(path)).rejects.toBeTruthy();

      expect(useAuthStore.getState().user).toEqual(user);
    },
  );

  it('leaves the session alone on a non-401 error', async () => {
    server.use(http.get('/api/jobs', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));

    await expect(api.get('/jobs')).rejects.toBeTruthy();

    expect(useAuthStore.getState().user).toEqual(user);
  });
});
