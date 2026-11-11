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
