import { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import { useToast } from '../components/common/Toast';
import { useAuthStore } from '../store/authStore';
import api from '../api/client';

interface UserDetail {
  id: string;
  email: string;
  display_name: string;
  role: string;
  created_at: string | null;
  last_login: string | null;
}

export default function UsersPage() {
  const toast = useToast();
  const { user: currentUser } = useAuthStore();
  const [users, setUsers] = useState<UserDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const fetchUsers = async () => {
    try {
      const { data } = await api.get('/admin/users');
      setUsers(data);
      setError('');
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      setError(status === 403 ? 'Admin access required' : 'Failed to load users');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchUsers(); }, []);

  const updateRole = async (userId: string, newRole: string) => {
    try {
      await api.patch(`/admin/users/${userId}`, { role: newRole });
      setUsers((prev) => prev.map((u) => u.id === userId ? { ...u, role: newRole } : u));
      toast.show('Role updated', 'success');
    } catch {
      toast.show('Failed to update role');
    }
  };

  const deleteUser = async (userId: string) => {
    try {
      await api.delete(`/admin/users/${userId}`);
      setUsers((prev) => prev.filter((u) => u.id !== userId));
      setConfirmDeleteId(null);
      toast.show('User deleted', 'success');
    } catch {
      toast.show('Failed to delete user');
    }
  };

  if (loading) return <p className="text-gray-500 text-sm p-4">Loading users...</p>;
  if (error) return <p className="text-red-600 dark:text-red-400 text-sm p-4">{error}</p>;

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Users</h2>

      <Card>
        <div className="space-y-2">
          {users.length === 0 ? (
            <p className="text-gray-500 text-sm">No users found.</p>
          ) : (
            users.map((u) => {
              const isSelf = u.id === currentUser?.id;
              return (
                <div key={u.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{u.display_name}</span>
                      <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${u.role === 'admin' ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>
                        {u.role}
                      </span>
                      {isSelf && <span className="text-xs text-gray-400">(you)</span>}
                    </div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{u.email}</div>
                    <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                      {u.last_login ? `Last login: ${new Date(u.last_login).toLocaleDateString()}` : 'Never logged in'}
                      {u.created_at && ` · Joined: ${new Date(u.created_at).toLocaleDateString()}`}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 ml-3 flex-shrink-0">
                    {!isSelf && (
                      <>
                        <select
                          value={u.role}
                          onChange={(e) => updateRole(u.id, e.target.value)}
                          className="rounded-lg border border-gray-300 dark:border-gray-600 text-xs p-1.5 bg-white dark:bg-gray-800 dark:text-gray-100"
                        >
                          <option value="user">User</option>
                          <option value="admin">Admin</option>
                        </select>
                        {confirmDeleteId === u.id ? (
                          <div className="flex items-center gap-1">
                            <span className="text-xs text-gray-500">Sure?</span>
                            <Button size="sm" variant="danger" onClick={() => deleteUser(u.id)}>Yes</Button>
                            <Button size="sm" variant="ghost" onClick={() => setConfirmDeleteId(null)}>No</Button>
                          </div>
                        ) : (
                          <Button size="sm" variant="ghost" onClick={() => setConfirmDeleteId(u.id)}>Delete</Button>
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
