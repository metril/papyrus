import api from './client';

export interface Webhook {
  id: number;
  name: string;
  url: string;
  events: string[];
  enabled: boolean;
  created_at: string;
}

export interface WebhookCreate {
  name: string;
  url: string;
  secret?: string;
  events: string[];
  enabled?: boolean;
}

export async function listWebhooks(): Promise<Webhook[]> {
  const { data } = await api.get('/webhooks');
  return data;
}

export async function listWebhookEvents(): Promise<string[]> {
  const { data } = await api.get('/webhooks/events');
  return data;
}

export async function createWebhook(body: WebhookCreate): Promise<Webhook> {
  const { data } = await api.post('/webhooks', body);
  return data;
}

export async function updateWebhook(id: number, body: WebhookCreate): Promise<Webhook> {
  const { data } = await api.put(`/webhooks/${id}`, body);
  return data;
}

export async function deleteWebhook(id: number): Promise<void> {
  await api.delete(`/webhooks/${id}`);
}
