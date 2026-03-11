import api from './client';
import type { ScanJob, ScanRequest } from '../types';

export async function getScannerStatus(): Promise<Record<string, unknown>> {
  const { data } = await api.get('/scanner/status');
  return data;
}

export async function getScannerOptions(): Promise<{
  resolutions: number[];
  modes: string[];
  formats: string[];
  sources: string[];
}> {
  const { data } = await api.get('/scanner/options');
  return data;
}

export async function initiateScan(request: ScanRequest): Promise<ScanJob> {
  const { data } = await api.post('/scanner/scan', request);
  return data;
}

export async function initiateBatchScan(request: ScanRequest): Promise<ScanJob> {
  const { data } = await api.post('/scanner/scan/batch', request);
  return data;
}

export async function listScans(): Promise<{ scans: ScanJob[]; total: number }> {
  const { data } = await api.get('/scanner/scans');
  return data;
}

export async function deleteScan(scanId: string): Promise<void> {
  await api.delete(`/scanner/scans/${scanId}`);
}

export function getScanDownloadUrl(scanId: string): string {
  return `/api/scanner/scans/${scanId}/download`;
}

export function getJobDownloadUrl(jobId: number): string {
  return `/api/jobs/${jobId}/download`;
}

export async function emailScan(
  scanId: string,
  to: string,
  subject?: string,
  body?: string,
): Promise<void> {
  await api.post(`/scanner/scans/${scanId}/email`, { to, subject, body });
}

export async function saveScanToSmb(
  scanId: string,
  shareId: number,
  remotePath?: string,
): Promise<void> {
  await api.post(`/scanner/scans/${scanId}/smb`, null, {
    params: { share_id: shareId, remote_path: remotePath },
  });
}

export async function saveScanToCloud(
  scanId: string,
  providerId: number,
): Promise<{ message: string }> {
  const { data } = await api.post(`/scanner/scans/${scanId}/cloud`, null, {
    params: { provider_id: providerId },
  });
  return data;
}
