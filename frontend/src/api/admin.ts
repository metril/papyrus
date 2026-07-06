import api from './client';

export async function getBackup(): Promise<Record<string, unknown>> {
  const { data } = await api.get('/admin/backup');
  return data;
}

export async function restoreBackup(body: unknown): Promise<void> {
  await api.post('/admin/restore', body);
}

// --- Dashboard stats ---

export interface DailyCount {
  day: string;
  count: number;
}

export interface DashboardStats {
  print_counts: Record<string, number>;
  scan_counts: Record<string, number>;
  daily_prints: DailyCount[];
  daily_scans: DailyCount[];
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const { data } = await api.get('/admin/stats');
  return data;
}

// --- Audit log ---

export interface AuditEntry {
  id: number;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  user_id: string | null;
  source: string;
  ip_address: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditLogParams {
  limit: number;
  offset: number;
  action?: string;
}

export async function getAuditLog(
  params: AuditLogParams,
): Promise<{ entries: AuditEntry[]; total: number }> {
  const { limit, offset, action } = params;
  const queryParams: Record<string, string | number> = { limit, offset };
  if (action) queryParams.action = action;
  const { data } = await api.get('/admin/audit', { params: queryParams });
  return data;
}

// --- User management ---

export interface UserDetail {
  id: string;
  email: string;
  display_name: string;
  role: string;
  created_at: string | null;
  last_login: string | null;
}

export async function listUsers(): Promise<UserDetail[]> {
  const { data } = await api.get('/admin/users');
  return data;
}

export async function updateUserRole(userId: string, role: string): Promise<UserDetail> {
  const { data } = await api.patch(`/admin/users/${userId}`, { role });
  return data;
}

export async function deleteUser(userId: string): Promise<void> {
  await api.delete(`/admin/users/${userId}`);
}
