import api from './client';

export async function getSettings(): Promise<Record<string, string>> {
  const { data } = await api.get('/settings');
  return data;
}

export async function updateSettings(values: Record<string, string>): Promise<void> {
  await api.put('/settings', values);
}

export async function sendTestEmail(): Promise<void> {
  await api.post('/email/test');
}

export interface EmailWebhookInfo {
  webhook_url: string;
  configured: boolean;
}

export async function getEmailWebhookInfo(): Promise<EmailWebhookInfo> {
  const { data } = await api.get('/email/webhook-info');
  return data;
}

export interface EmailWebhookSecret {
  secret: string;
  webhook_url: string;
}

export async function generateEmailWebhookSecret(): Promise<EmailWebhookSecret> {
  const { data } = await api.post('/email/webhook-secret');
  return data;
}
