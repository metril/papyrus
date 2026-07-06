import api from './client';

export async function getBackup(): Promise<Record<string, unknown>> {
  const { data } = await api.get('/admin/backup');
  return data;
}

export async function restoreBackup(body: unknown): Promise<void> {
  await api.post('/admin/restore', body);
}
