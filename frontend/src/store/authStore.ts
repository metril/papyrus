import { create } from 'zustand';
import type { User } from '../types';
import api from '../api/client';
import { queryClient } from '../api/queryClient';

interface AuthState {
  user: User | null;
  loading: boolean;
  error: string | null;
  fetchUser: () => Promise<void>;
  /**
   * Clears the local session state only — makes App/AppShell render
   * LoginScreen. Used both by `logout()` below and by the axios response
   * interceptor (F16) when a non-auth endpoint 401s (an expired/invalid
   * session), so either path lands the user back on LoginScreen instead of
   * a hard navigation to a route that may not even be reachable (OIDC
   * disabled, dev mode off). Also clears the Query cache (F83): the cached
   * data is user-scoped server-side (cloud providers, scan profiles,
   * admin-only token lists, ...) and must not leak into whoever signs in
   * next on a shared print station.
   */
  signOut: () => void;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  loading: true,
  error: null,

  fetchUser: async () => {
    try {
      set({ loading: true, error: null });
      const response = await api.get('/auth/me');
      set({ user: response.data, loading: false });
    } catch {
      set({ user: null, loading: false });
    }
  },

  signOut: () => {
    queryClient.clear();
    set({ user: null });
  },

  logout: async () => {
    try {
      await api.post('/auth/logout');
    } catch {
      // F83: a failed logout POST must not block signing out locally, and
      // must not surface as an unhandled rejection.
    } finally {
      get().signOut();
    }
  },
}));
