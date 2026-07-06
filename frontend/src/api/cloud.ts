import api from './client';
import type { CloudProvider, CloudFileEntry } from '../types';

export async function listProviders(): Promise<CloudProvider[]> {
  const { data } = await api.get('/cloud/providers');
  return data.providers;
}

export async function disconnectProvider(id: number): Promise<void> {
  await api.delete(`/cloud/disconnect/${id}`);
}

export interface WebdavConnect {
  url: string;
  username: string;
  password: string;
}

export async function connectWebdav(body: WebdavConnect): Promise<void> {
  await api.post('/webdav/connect', body);
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

export async function downloadCloudFile(
  providerId: number,
  fileId: string,
  isDropbox: boolean,
  filename: string,
  mimeType?: string,
): Promise<Blob> {
  const params = new URLSearchParams();
  if (isDropbox) {
    params.set('path', fileId);
  } else {
    params.set('file_id', fileId);
  }
  params.set('filename', filename);
  if (mimeType) {
    params.set('mime_type', mimeType);
  }
  const { data } = await api.get(`/cloud/download/${providerId}?${params.toString()}`, {
    responseType: 'blob',
  });
  return data;
}

export function getDownloadUrl(
  providerId: number,
  fileId: string,
  isDropbox: boolean = false,
  filename?: string,
  mimeType?: string,
): string {
  const params = new URLSearchParams();
  if (isDropbox) {
    params.set('path', fileId);
  } else {
    params.set('file_id', fileId);
  }
  if (filename) {
    params.set('filename', filename);
  }
  if (mimeType) {
    params.set('mime_type', mimeType);
  }
  return `/api/cloud/download/${providerId}?${params.toString()}`;
}
