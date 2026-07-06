import api from './client';
import type { APIToken, APITokenCreated } from '../types';

export async function listApiTokens(): Promise<APIToken[]> {
  const { data } = await api.get('/auth/tokens');
  return data;
}

export interface ApiTokenCreate {
  name: string;
  permissions: string[];
  expires_in_days: number | null;
}

export async function createApiToken(body: ApiTokenCreate): Promise<APITokenCreated> {
  const { data } = await api.post('/auth/tokens', body);
  return data;
}

export async function revokeApiToken(id: string): Promise<void> {
  await api.delete(`/auth/tokens/${id}`);
}
