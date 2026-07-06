import api from './client';
import type { SMBShare, SMBFileEntry } from '../types';

export async function listSmbShares(): Promise<SMBShare[]> {
  const { data } = await api.get('/smb/shares');
  return data;
}

export async function browseSmb(shareId: number, path: string): Promise<SMBFileEntry[]> {
  const { data } = await api.get(`/smb/browse/${shareId}`, { params: { path } });
  return data;
}

export async function downloadSmbFile(shareId: number, path: string): Promise<Blob> {
  const { data } = await api.get(`/smb/download/${shareId}`, {
    params: { path },
    responseType: 'blob',
  });
  return data;
}
