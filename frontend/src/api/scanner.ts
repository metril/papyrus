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

export async function bulkDeleteScans(scanIds: string[]): Promise<void> {
  await api.post('/scanner/scans/bulk-delete', { scan_ids: scanIds });
}

export function getScanDownloadUrl(scanId: string): string {
  return `/api/scanner/scans/${scanId}/download`;
}

export function getScanThumbnailUrl(scanId: string): string {
  return `/api/scanner/scans/${scanId}/thumbnail`;
}

export function getJobDownloadUrl(jobId: number): string {
  return `/api/jobs/${jobId}/download`;
}

export function getJobPreviewUrl(jobId: number): string {
  return `/api/jobs/${jobId}/preview`;
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

// --- Scan side-actions (paperless / enhance / OCR / collate) ---
// Response shapes aren't guaranteed to be full ScanJobs, so callers invalidate
// the scans list rather than upsert a returned object.

export async function sendScanToPaperless(scanId: string): Promise<void> {
  await api.post(`/scanner/scans/${scanId}/paperless`);
}

export interface ScanEnhanceOptions {
  brightness: number;
  contrast: number;
  rotation: number;
  auto_crop: boolean;
  deskew: boolean;
}

export async function enhanceScan(scanId: string, options: ScanEnhanceOptions): Promise<void> {
  await api.post(`/scanner/scans/${scanId}/enhance`, options);
}

export async function ocrScan(scanId: string): Promise<void> {
  await api.post(`/scanner/scans/${scanId}/ocr`);
}

export async function collateScans(scanIds: string[]): Promise<void> {
  await api.post('/scanner/collate', { scan_ids: scanIds });
}

// --- Scan profiles ---

export interface ScanProfile {
  id: number;
  name: string;
  resolution: number;
  color_mode: string;
  format: string;
  source: string;
  ocr_enabled: boolean;
}

export interface ScanProfileCreate {
  name: string;
  resolution: number;
  color_mode: string;
  format: string;
  source: string;
  ocr_enabled: boolean;
}

export async function listScanProfiles(): Promise<ScanProfile[]> {
  const { data } = await api.get('/scanner/profiles');
  return data;
}

export async function createScanProfile(body: ScanProfileCreate): Promise<ScanProfile> {
  const { data } = await api.post('/scanner/profiles', body);
  return data;
}

export async function deleteScanProfile(id: number): Promise<void> {
  await api.delete(`/scanner/profiles/${id}`);
}
