import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/mocks/server';
import AppShell from './AppShell';
import { useAuthStore } from '../../store/authStore';

// AppShell mounts useRealtimeBridge (3 WebSocket channels) once a user is
// authenticated — stub the global WebSocket so those connect against a
// no-op mock instead of a real (nonexistent, in jsdom) network socket.
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
}

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper() {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/print']}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/print" element={<div>Print page content</div>} />
              <Route path="/history" element={<div>History page content</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
  };
}

describe('AppShell', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, loading: true, error: null });
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe('unauthenticated (F16)', () => {
    it('renders LoginScreen — not a raw JSON error page — when /auth/me 401s, with no forced navigation', async () => {
      server.use(
        http.get('/api/auth/me', () => HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })),
        http.get('/api/auth/providers', () =>
          HttpResponse.json({ local_enabled: true, oidc_enabled: false, admin_override: false }),
        ),
      );

      const Wrapper = makeWrapper();
      render(<Wrapper />);

      await waitFor(() => expect(screen.getByText('Print & Scan Server')).toBeInTheDocument());
      expect(screen.getByPlaceholderText('Username')).toBeInTheDocument();
      expect(screen.getByPlaceholderText('Password')).toBeInTheDocument();
      // No OIDC-only install, so no SSO button and no forced redirect either.
      expect(screen.queryByText('Sign in with SSO')).not.toBeInTheDocument();
    });

    it('shows the SSO button (not a hard redirect) when providers report OIDC as the only method', async () => {
      server.use(
        http.get('/api/auth/me', () => HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })),
        http.get('/api/auth/providers', () =>
          HttpResponse.json({ local_enabled: false, oidc_enabled: true, admin_override: false }),
        ),
      );

      const Wrapper = makeWrapper();
      render(<Wrapper />);

      await waitFor(() => expect(screen.getByText('Sign in with SSO')).toBeInTheDocument());
      expect(screen.queryByPlaceholderText('Username')).not.toBeInTheDocument();
    });

    it('a wrong local-login password shows the error message in place, instead of navigating away from it', async () => {
      server.use(
        http.get('/api/auth/me', () => HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 })),
        http.get('/api/auth/providers', () =>
          HttpResponse.json({ local_enabled: true, oidc_enabled: false, admin_override: false }),
        ),
        http.post('/api/auth/local-login', () =>
          HttpResponse.json({ detail: 'Invalid credentials' }, { status: 401 }),
        ),
      );

      const user = userEvent.setup();
      const Wrapper = makeWrapper();
      render(<Wrapper />);

      await waitFor(() => expect(screen.getByPlaceholderText('Username')).toBeInTheDocument());
      await user.type(screen.getByPlaceholderText('Username'), 'alice');
      await user.type(screen.getByPlaceholderText('Password'), 'wrong');
      await user.click(screen.getByRole('button', { name: 'Sign in' }));

      await waitFor(() => expect(screen.getByText('Invalid username or password')).toBeInTheDocument());
      // Still on the login form — the form fields are still there to retry.
      expect(screen.getByPlaceholderText('Username')).toBeInTheDocument();
    });
  });

  describe('mobile bottom navigation (F88)', () => {
    function mobileNav(container: HTMLElement): HTMLElement {
      const navs = container.querySelectorAll('nav');
      // The desktop sidebar's <nav> is rendered first in the DOM; the
      // mobile bottom bar (`md:hidden`, present regardless of viewport in
      // jsdom) is second.
      return navs[1] as HTMLElement;
    }

    beforeEach(() => {
      server.use(
        http.get('/api/auth/me', () =>
          HttpResponse.json({ id: 'u1', email: 'admin@example.com', display_name: 'Admin', role: 'admin' }),
        ),
      );
    });

    it('shows only the first 4 nav items plus a "More" button — not the old hardcoded 3 + pinned Settings', async () => {
      const Wrapper = makeWrapper();
      const { container } = render(<Wrapper />);

      await waitFor(() => expect(screen.getByText('Print page content')).toBeInTheDocument());

      const nav = mobileNav(container);
      expect(within(nav).getByRole('link', { name: /Print/ })).toBeInTheDocument();
      expect(within(nav).getByRole('link', { name: /Scan/ })).toBeInTheDocument();
      expect(within(nav).getByRole('link', { name: /Copy/ })).toBeInTheDocument();
      expect(within(nav).getByRole('link', { name: /Files/ })).toBeInTheDocument();
      // History, Dashboard, Users, Audit, Settings are all overflow now.
      expect(within(nav).queryByRole('link', { name: /^History/ })).not.toBeInTheDocument();
      expect(within(nav).queryByRole('link', { name: /^Settings/ })).not.toBeInTheDocument();
      expect(within(nav).getByRole('button', { name: /More/ })).toBeInTheDocument();
    });

    it('"More" opens a sheet with every remaining item, including Settings and History', async () => {
      const user = userEvent.setup();
      const Wrapper = makeWrapper();
      const { container } = render(<Wrapper />);

      await waitFor(() => expect(screen.getByText('Print page content')).toBeInTheDocument());

      await user.click(within(mobileNav(container)).getByRole('button', { name: /More/ }));

      const sheet = screen.getByRole('dialog', { name: 'More navigation' });
      expect(within(sheet).getByRole('link', { name: /History/ })).toBeInTheDocument();
      expect(within(sheet).getByRole('link', { name: /Dashboard/ })).toBeInTheDocument();
      expect(within(sheet).getByRole('link', { name: /Users/ })).toBeInTheDocument();
      expect(within(sheet).getByRole('link', { name: /Audit/ })).toBeInTheDocument();
      expect(within(sheet).getByRole('link', { name: /Settings/ })).toBeInTheDocument();
    });

    it('navigating from the sheet closes it', async () => {
      const user = userEvent.setup();
      const Wrapper = makeWrapper();
      const { container } = render(<Wrapper />);

      await waitFor(() => expect(screen.getByText('Print page content')).toBeInTheDocument());
      await user.click(within(mobileNav(container)).getByRole('button', { name: /More/ }));
      const sheet = screen.getByRole('dialog', { name: 'More navigation' });
      await user.click(within(sheet).getByRole('link', { name: /History/ }));

      await waitFor(() => expect(screen.getByText('History page content')).toBeInTheDocument());
      expect(screen.queryByRole('dialog', { name: 'More navigation' })).not.toBeInTheDocument();
    });
  });
});
