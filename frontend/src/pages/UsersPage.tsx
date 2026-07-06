import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Users } from 'lucide-react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import Skeleton from '../components/common/Skeleton';
import EmptyState from '../components/common/EmptyState';
import ErrorState from '../components/common/ErrorState';
import { useToast } from '../hooks/useToast';
import { useAuthStore } from '../store/authStore';
import { useUsers, queryKeys } from '../api/queries';
import { updateUserRole, deleteUser } from '../api/admin';

/** Mirrors the pre-Query users-list failure copy. */
function describeUsersLoadError(error: unknown): string {
  if (axios.isAxiosError(error) && error.response?.status === 403) {
    return 'Admin access required';
  }
  return 'Failed to load users';
}

export default function UsersPage() {
  const toast = useToast();
  const currentUser = useAuthStore((s) => s.user);
  const queryClient = useQueryClient();
  const { data: users = [], isLoading: loading, isError, error: queryError, refetch } = useUsers();
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const error = isError ? describeUsersLoadError(queryError) : '';

  const updateRoleMutation = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) => updateUserRole(userId, role),
    meta: { suppressGlobalError: true },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users });
      toast.show('Role updated', 'success');
    },
    onError: () => toast.show('Failed to update role'),
  });

  const deleteUserMutation = useMutation({
    mutationFn: (userId: string) => deleteUser(userId),
    meta: { suppressGlobalError: true },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users });
      setConfirmDeleteId(null);
      toast.show('User deleted', 'success');
    },
    onError: () => toast.show('Failed to delete user'),
  });

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Users</h2>
        <Card>
          <Skeleton variant="row" count={4} />
        </Card>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Users</h2>
        <ErrorState title={error} onRetry={() => refetch()} />
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Users</h2>

      <Card>
        <div className="space-y-2">
          {users.length === 0 ? (
            <EmptyState
              icon={Users}
              title="No users found"
              hint="Users appear here after they sign in via OIDC."
            />
          ) : (
            users.map((u) => {
              const isSelf = u.id === currentUser?.id;
              return (
                <div key={u.id} className="flex flex-col gap-3 rounded-lg border border-gray-200 p-4 dark:border-gray-700 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{u.display_name}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${u.role === 'admin' ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>
                        {u.role}
                      </span>
                      {isSelf && <span className="text-xs text-gray-400 dark:text-gray-500">(you)</span>}
                    </div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{u.email}</div>
                    <div className="mt-0.5 font-mono text-xs text-gray-400 dark:text-gray-500">
                      {u.last_login ? `Last login: ${new Date(u.last_login).toLocaleDateString()}` : 'Never logged in'}
                      {u.created_at && ` · Joined: ${new Date(u.created_at).toLocaleDateString()}`}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 sm:ml-4 sm:shrink-0">
                    {!isSelf && (
                      <>
                        <select
                          value={u.role}
                          onChange={(e) => updateRoleMutation.mutate({ userId: u.id, role: e.target.value })}
                          className="rounded-lg border border-gray-300 dark:border-gray-600 text-xs p-1.5 bg-white dark:bg-gray-800 dark:text-gray-100"
                        >
                          <option value="user">User</option>
                          <option value="admin">Admin</option>
                        </select>
                        {confirmDeleteId === u.id ? (
                          <div className="flex items-center gap-1">
                            <span className="text-xs text-gray-500 dark:text-gray-400">Sure?</span>
                            <Button size="sm" variant="danger" onClick={() => deleteUserMutation.mutate(u.id)}>Yes</Button>
                            <Button size="sm" variant="ghost" onClick={() => setConfirmDeleteId(null)}>No</Button>
                          </div>
                        ) : (
                          <Button size="sm" variant="danger-ghost" onClick={() => setConfirmDeleteId(u.id)}>Delete</Button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </Card>
    </div>
  );
}
