import api from './client';
import type { PrinterStatus, PrintJob } from '../types';

export async function getPrinterStatus(): Promise<PrinterStatus> {
  const { data } = await api.get('/printer/status');
  return data;
}

export async function getPrinterSettings(): Promise<Record<string, unknown>> {
  const { data } = await api.get('/printer/settings');
  return data;
}

export async function uploadPrintJob(
  file: File,
  options: { copies: number; duplex: boolean; media: string; hold: boolean },
): Promise<PrintJob> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('copies', String(options.copies));
  formData.append('duplex', String(options.duplex));
  formData.append('media', options.media);
  formData.append('hold', String(options.hold));

  const { data } = await api.post('/jobs/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
}

export async function releaseJob(jobId: number, pin?: string): Promise<PrintJob> {
  const { data } = await api.post(`/jobs/${jobId}/release`, pin ? { pin } : {});
  return data;
}

export async function cancelJob(jobId: number): Promise<PrintJob> {
  const { data } = await api.post(`/jobs/${jobId}/cancel`);
  return data;
}

export async function deleteJob(jobId: number): Promise<void> {
  await api.delete(`/jobs/${jobId}`);
}

export async function reprintJob(jobId: number): Promise<PrintJob> {
  const { data } = await api.post(`/jobs/${jobId}/reprint`);
  return data;
}

export async function listJobs(status?: string): Promise<{ jobs: PrintJob[]; total: number }> {
  const params = status ? { status } : {};
  const { data } = await api.get('/jobs', { params });
  return data;
}
