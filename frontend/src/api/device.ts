import api from './client';

export interface DeviceLookup {
  client_name: string;
  scopes: string[];
  token_ttl_minutes: number;
  expires_at: string;
  status: string;
}

export async function lookupDeviceCode(code: string): Promise<DeviceLookup> {
  const { data } = await api.get(`/auth/device/${encodeURIComponent(code)}`);
  return data;
}

export async function decideDevice(userCode: string, approve: boolean): Promise<{ status: string }> {
  const { data } = await api.post('/auth/device/approve', { user_code: userCode, approve });
  return data;
}

export interface DeviceRequest {
  id: string;
  client_name: string;
  choices: number[];
  expires_at: string;
}

export async function listDeviceRequests(): Promise<DeviceRequest[]> {
  const { data } = await api.get('/auth/device/requests');
  return data;
}

export async function answerDeviceRequest(id: string, matchCode: number): Promise<{ status: string }> {
  const { data } = await api.post(`/auth/device/requests/${id}/approve`, { match_code: matchCode });
  return data;
}

export async function denyDeviceRequest(id: string): Promise<{ status: string }> {
  const { data } = await api.post(`/auth/device/requests/${id}/deny`);
  return data;
}

export interface KioskPinStatus {
  enabled: boolean;
  pin_length: number;
  has_pin: boolean;
}

export async function getKioskPinStatus(): Promise<KioskPinStatus> {
  const { data } = await api.get('/auth/me/kiosk-pin');
  return data;
}

export async function setKioskPin(pin: string): Promise<void> {
  await api.put('/auth/me/kiosk-pin', { pin });
}

export async function clearKioskPin(): Promise<void> {
  await api.delete('/auth/me/kiosk-pin');
}
