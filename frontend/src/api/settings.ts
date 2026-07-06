import api from './client';

export async function getSettings(): Promise<Record<string, string>> {
  const { data } = await api.get('/settings');
  return data;
}
