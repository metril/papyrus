import type { PrintJob, User } from '../types';

/**
 * True when `job` is PIN-protected and belongs to neither the viewer nor an
 * admin — the file endpoints (download/preview/thumbnail) 403 those requests
 * without a PIN (F27), so any UI surfacing a print job's file (JobRow,
 * HistoryRow, ...) must not attempt them for a job in this state: no
 * thumbnail `<img>`, no preview/download trigger. A job with no owner
 * (network jobs) is never locked.
 *
 * Shared between JobRow and HistoryRow rather than duplicated — both render
 * print-job thumbnails and both must agree on when a job is locked for the
 * current viewer.
 */
export function isLockedForViewer(job: PrintJob, viewer: User | null): boolean {
  if (!job.has_pin || job.user_id == null) return false;
  if (viewer?.role === 'admin') return false;
  return job.user_id !== viewer?.id;
}
