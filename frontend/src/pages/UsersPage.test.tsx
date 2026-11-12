import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../test/mocks/server';
import { useAuthStore } from '../store/authStore';
import { useToastStore } from '../store/toastStore';
import UsersPage from './UsersPage';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

const me = {
  id: 'me-1',
  email: 'me@example.com',
  display_name: 'Current Admin',
  role: 'admin',
  created_at: '2026-01-01T00:00:00Z',
  last_login: '2026-07-01T00:00:00Z',
  is_active: true,
  kiosk_enabled: true,
};

const other = {
  id: 'other-1',
  email: 'other@example.com',
  display_name: 'Other User',
  role: 'user',
  created_at: '2026-01-02T00:00:00Z',
  last_login: null,
  is_active: true,
  kiosk_enabled: true,
};

describe('UsersPage', () => {
  it('changing a role selects fires a PATCH and refetches the users list', async () => {
    useAuthStore.setState({ user: { id: me.id, email: me.email, display_name: me.display_name, role: me.role } });

    let getCount = 0;
    let patchBody: unknown = null;

    server.use(
      http.get('/api/v1/admin/users', () => {
        getCount += 1;
        return HttpResponse.json(
          getCount === 1 ? [me, other] : [me, { ...other, role: 'admin' }],
        );
      }),
      http.patch('/api/v1/admin/users/other-1', async ({ request }) => {
        patchBody = await request.json();
        return HttpResponse.json({ id: 'other-1', role: 'admin' });
      }),
    );

    const user = userEvent.setup();
    render(<UsersPage />, { wrapper: makeWrapper() });

    await waitFor(() => expect(screen.getByText('Other User')).toBeInTheDocument());

    // Self-protection: the current user's row has no role select or delete
    // button, so exactly one combobox (Other User's) is on the page.
    expect(screen.getByText('(you)')).toBeInTheDocument();
    const roleSelect = screen.getByRole('combobox');

    await user.selectOptions(roleSelect, 'admin');

    await waitFor(() => expect(patchBody).toEqual({ role: 'admin' }));
    await waitFor(() => expect(getCount).toBe(2));

    // Refetch (triggered by the mutation's onSuccess invalidation) reflects
    // the new role for Other User.
    await waitFor(() => expect(roleSelect).toHaveValue('admin'));
  });

  it('toggles Active and Kiosk via PATCH, dims inactive users, and toasts a 409', async () => {
    useAuthStore.setState({ user: { id: me.id, email: me.email, display_name: me.display_name, role: me.role } });
    const bodies: unknown[] = [];
    let inactive = false;
    server.use(
      http.get('/api/v1/admin/users', () =>
        HttpResponse.json([me, { ...other, is_active: !inactive }]),
      ),
      http.patch('/api/v1/admin/users/other-1', async ({ request }) => {
        const body = (await request.json()) as { is_active?: boolean };
        bodies.push(body);
        if (body.is_active === true) {
          return HttpResponse.json({ detail: 'Cannot deactivate the last active admin' }, { status: 409 });
        }
        if (body.is_active === false) inactive = true;
        return HttpResponse.json({ id: 'other-1', role: 'user', is_active: false, kiosk_enabled: true });
      }),
    );

    const user = userEvent.setup();
    render(<UsersPage />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText('Other User')).toBeInTheDocument());

    const switches = screen.getAllByRole('switch');
    expect(switches).toHaveLength(4);
    // Self row: Active is disabled, Kiosk is not.
    expect(switches[0]).toBeDisabled();
    expect(switches[1]).toBeEnabled();

    await user.click(switches[3]);
    await waitFor(() => expect(bodies[0]).toEqual({ kiosk_enabled: false }));

    await user.click(screen.getAllByRole('switch')[2]);
    await waitFor(() => expect(bodies[1]).toEqual({ is_active: false }));
    await waitFor(() => expect(document.querySelector('[data-inactive="true"]')).not.toBeNull());
    expect(screen.getByText('inactive')).toBeInTheDocument();

    // Re-activating hits the mocked guard error.
    await user.click(screen.getAllByRole('switch')[2]);
    await waitFor(() => expect(bodies).toHaveLength(3));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
        'Cannot deactivate the last active admin',
      ),
    );
  });
});
