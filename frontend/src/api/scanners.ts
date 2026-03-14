import api from './client';
import type { ManagedScanner, ManagedScannerCreate, ManagedScannerUpdate, DiscoveredDevice } from '../types';

export async function listScanners(): Promise<ManagedScanner[]> {
  const { data } = await api.get('/scanners');
  return data;
}

export async function addScanner(body: ManagedScannerCreate): Promise<ManagedScanner> {
  const { data } = await api.post('/scanners', body);
  return data;
}

export async function updateScanner(id: number, body: ManagedScannerUpdate): Promise<ManagedScanner> {
  const { data } = await api.patch(`/scanners/${id}`, body);
  return data;
}

export async function deleteScanner(id: number): Promise<void> {
  await api.delete(`/scanners/${id}`);
}

export async function setDefaultScanner(id: number): Promise<ManagedScanner> {
  const { data } = await api.post(`/scanners/${id}/default`);
  return data;
}

export async function discoverScanners(): Promise<DiscoveredDevice[]> {
  const { data } = await api.get('/scanners/discover');
  return data;
}

export interface ProbeResult {
  reachable: boolean;
  device: string;
  make_model: string | null;
  error: string | null;
}

export async function probeScanner(ip: string): Promise<ProbeResult> {
  const { data } = await api.get('/scanners/probe', { params: { ip } });
  return data;
}

export interface ScannerTestResult {
  device: string;
  escl_ok: boolean;
  escl_error: string | null;
  sane_ok: boolean;
  sane_error: string | null;
  make_model: string | null;
}

export async function testScanner(id: number): Promise<ScannerTestResult> {
  const { data } = await api.get(`/scanners/${id}/test`);
  return data;
}
