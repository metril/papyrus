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

// --- WebDAV browsing (F82) ---
//
// A WebDAV-connected provider (Nextcloud, ...) is a `CloudProvider` row like
// any other, but it's served by its own router (`/api/webdav/*`) rather than
// `/api/cloud/*` — that router has no browse-by-provider-id dispatch for
// "webdav", so routing a webdav provider through `listFiles`/`getDownloadUrl`
// above 400s. The WebDAV service also has no generic file-download endpoint
// (only listing and a scan-upload target), so browsing a webdav provider
// supports listing/navigating folders but not downloading/printing a file.

interface WebdavRawEntry {
  name: string;
  path: string;
  is_directory: boolean;
  size: number | null;
  modified_at: string | null;
  mime_type: string | null;
}

export async function listWebdavFiles(providerId: number, path: string): Promise<CloudFileEntry[]> {
  const { data } = await api.get<WebdavRawEntry[]>(`/webdav/${providerId}/files`, {
    params: { path },
  });
  // The WebDAV service identifies entries by their full server path rather
  // than an opaque id — reused as CloudFileEntry.id, which is exactly what
  // list_webdav_files expects back as the next `path` when navigating in.
  return data.map((entry) => ({
    name: entry.name,
    id: entry.path,
    is_directory: entry.is_directory,
    size: entry.size,
    modified_at: entry.modified_at,
    mime_type: entry.mime_type,
  }));
}

export async function uploadScanToWebdav(
  providerId: number,
  scanId: string,
  destinationFolder = '/',
): Promise<{ message: string }> {
  const { data } = await api.post(`/webdav/${providerId}/upload`, {
    scan_id: scanId,
    destination_folder: destinationFolder,
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
