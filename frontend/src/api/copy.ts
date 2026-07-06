import api from './client';
import type { CopyRequest } from '../types';

export interface CopyResult {
  message: string;
  scan_id: string;
  cups_job_id: number | null;
}

export async function startCopy(options: CopyRequest): Promise<CopyResult> {
  const { data } = await api.post('/copy', options);
  return data;
}
