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

/**
 * PATCH-shaped body for `updateWebhook` (F18): every field is optional, and
 * an omitted field leaves the stored value unchanged — the backend can't
 * tell "not sent" from "sent as undefined" either way, since axios/JSON
 * both drop `undefined` keys. Used so a toggle-enabled call can send just
 * `{enabled}` without round-tripping (and risking clobbering) the rest —
 * `secret` in particular is never returned by `listWebhooks`, so there's
 * nothing for the client to echo back.
 */
export type WebhookUpdate = Partial<WebhookCreate>;

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

export async function updateWebhook(id: number, body: WebhookUpdate): Promise<Webhook> {
  const { data } = await api.put(`/webhooks/${id}`, body);
  return data;
}

export async function deleteWebhook(id: number): Promise<void> {
  await api.delete(`/webhooks/${id}`);
}
