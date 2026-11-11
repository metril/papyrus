import api from './client';
import type { Contact, KioskResponse, KioskTokenResponse } from '../types';

export async function listKiosks(): Promise<KioskResponse[]> {
  const { data } = await api.get('/kiosks');
  return data;
}

export async function createKiosk(name: string): Promise<KioskTokenResponse> {
  const { data } = await api.post('/kiosks', { name });
  return data;
}

export async function deleteKiosk(id: string): Promise<void> {
  await api.delete(`/kiosks/${id}`);
}

export async function rotateKioskToken(id: string): Promise<KioskTokenResponse> {
  const { data } = await api.post(`/kiosks/${id}/rotate-token`);
  return data;
}

export async function listContacts(): Promise<Contact[]> {
  const { data } = await api.get('/contacts');
  return data;
}

export async function createContact(body: { name: string; email: string }): Promise<Contact> {
  const { data } = await api.post('/contacts', body);
  return data;
}

export async function updateContact(id: string, body: { name: string; email: string }): Promise<Contact> {
  const { data } = await api.put(`/contacts/${id}`, body);
  return data;
}

export async function deleteContact(id: string): Promise<void> {
  await api.delete(`/contacts/${id}`);
}
