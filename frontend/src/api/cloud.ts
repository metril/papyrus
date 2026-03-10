import api from './client';
import type { CloudProvider, CloudFileEntry } from '../types';

export async function listProviders(): Promise<CloudProvider[]> {
  const { data } = await api.get('/cloud/providers');
  return data.providers;
}

export async function disconnectProvider(id: number): Promise<void> {
  await api.delete(`/cloud/disconnect/${id}`);
}

export function getAuthorizeUrl(provider: string): string {
  return `/api/cloud/authorize/${provider}`;
}

export async function listFiles(
  providerId: number,
  params: { folder_id?: string; path?: string },
): Promise<CloudFileEntry[]> {
  const { data } = await api.get(`/cloud/files/${providerId}`, { params });
  return data;
}

export function getDownloadUrl(
  providerId: number,
  fileId: string,
  isDropbox: boolean = false,
): string {
  if (isDropbox) {
    return `/api/cloud/download/${providerId}?path=${encodeURIComponent(fileId)}`;
  }
  return `/api/cloud/download/${providerId}?file_id=${encodeURIComponent(fileId)}`;
}
