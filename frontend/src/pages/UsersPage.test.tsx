import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../test/mocks/server';
import { useAuthStore } from '../store/authStore';
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
};

const other = {
  id: 'other-1',
  email: 'other@example.com',
  display_name: 'Other User',
  role: 'user',
  created_at: '2026-01-02T00:00:00Z',
  last_login: null,
};

describe('UsersPage', () => {
  it('changing a role selects fires a PATCH and refetches the users list', async () => {
    useAuthStore.setState({ user: { id: me.id, email: me.email, display_name: me.display_name, role: me.role } });

    let getCount = 0;
    let patchBody: unknown = null;

    server.use(
      http.get('/api/admin/users', () => {
        getCount += 1;
        return HttpResponse.json(
          getCount === 1 ? [me, other] : [me, { ...other, role: 'admin' }],
        );
      }),
      http.patch('/api/admin/users/other-1', async ({ request }) => {
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
});
