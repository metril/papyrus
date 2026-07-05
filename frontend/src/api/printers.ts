import api from './client';
import type { DiscoveredPrinter, ManagedPrinter, ManagedPrinterCreate, ManagedPrinterUpdate } from '../types';

export async function listPrinters(): Promise<ManagedPrinter[]> {
  const { data } = await api.get('/printers');
  return data;
}

export async function addPrinter(body: ManagedPrinterCreate): Promise<ManagedPrinter> {
  const { data } = await api.post('/printers', body);
  return data;
}

export async function updatePrinter(id: number, body: ManagedPrinterUpdate): Promise<ManagedPrinter> {
  const { data } = await api.patch(`/printers/${id}`, body);
  return data;
}

export async function deletePrinter(id: number): Promise<void> {
  await api.delete(`/printers/${id}`);
}

export async function setDefaultPrinter(id: number): Promise<ManagedPrinter> {
  const { data } = await api.post(`/printers/${id}/default`);
  return data;
}

export async function assignJobPrinter(jobId: number, printerId: number): Promise<void> {
  await api.patch(`/jobs/${jobId}/printer`, { printer_id: printerId });
}

export interface PrinterProbeResult {
  reachable: boolean;
  uri: string;
  make_model: string | null;
  location: string | null;
  state: number | null;
  suggested_display_name: string | null;
}

export async function probePrinter(ip: string): Promise<PrinterProbeResult> {
  const { data } = await api.get('/printers/probe', { params: { ip } });
  return data;
}

export async function resumePrinter(id: number): Promise<ManagedPrinter> {
  const { data } = await api.post(`/printers/${id}/resume`);
  return data;
}

export async function discoverPrinters(): Promise<DiscoveredPrinter[]> {
  const { data } = await api.get('/printers/discover');
  return data.printers;
}

export async function printTestPage(id: number): Promise<void> {
  await api.post(`/printers/${id}/test-page`);
}

export async function refreshPrinterInfo(id: number): Promise<ManagedPrinter> {
  const { data } = await api.post(`/printers/${id}/refresh-info`);
  return data;
}
