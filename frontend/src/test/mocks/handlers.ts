import { http, HttpResponse, type RequestHandler } from 'msw';
import type { DashboardStats, TrendPoint, UserUsage } from '../../api/admin';

/** Build a zero-filled 30-day trend, mirroring the backend shape, with an
 * optional map of `MM-DD` offsets → counts for the days that had activity. */
export function makeTrend(activity: Record<string, { prints?: number; scans?: number }> = {}): TrendPoint[] {
  const start = new Date(Date.UTC(2026, 5, 7)); // 2026-06-07
  return Array.from({ length: 30 }, (_, i) => {
    const day = new Date(start);
    day.setUTCDate(start.getUTCDate() + i);
    const date = day.toISOString().slice(0, 10);
    const hit = activity[date] ?? {};
    return { date, prints: hit.prints ?? 0, scans: hit.scans ?? 0 };
  });
}

const DEFAULT_PER_USER: UserUsage[] = [
  { username: 'alice', prints: 42, scans: 12 },
  { username: 'bob', prints: 18, scans: 30 },
  { username: 'Network', prints: 7, scans: 5 },
];

export function makeDashboardStats(overrides: Partial<DashboardStats> = {}): DashboardStats {
  return {
    print_counts: { held: 2, released: 40, failed: 1, printing: 0 },
    scan_counts: { completed: 47, failed: 0 },
    daily_prints: [],
    daily_scans: [],
    trend_30d: makeTrend({ '2026-06-10': { prints: 5, scans: 2 }, '2026-07-06': { prints: 8, scans: 6 } }),
    per_user: DEFAULT_PER_USER,
    ...overrides,
  };
}

export const handlers: RequestHandler[] = [
  http.get('/api/jobs', () => {
    return HttpResponse.json({ jobs: [], total: 0 });
  }),
  http.get('/api/admin/stats', () => {
    return HttpResponse.json(makeDashboardStats());
  }),
  http.get('/api/printer/status', () => {
    return HttpResponse.json({
      state: 3,
      state_message: 'Ready',
      accepting_jobs: true,
      markers: [{ name: 'Black Toner', level: 64, color: 'black' }],
      state_reasons: ['none'],
    });
  }),
];
