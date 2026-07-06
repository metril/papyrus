import { useQuery } from '@tanstack/react-query';
import { listJobs, getPrinterStatus } from './printer';
import { listScans } from './scanner';
import { listPrinters } from './printers';
import { getSettings, getEmailWebhookInfo } from './settings';

/**
 * Single source of truth for React Query cache keys. Every hook derives its key
 * from here — never inline a key array at a call site.
 */
export const queryKeys = {
  jobs: {
    all: ['jobs'] as const,
    list: () => ['jobs', 'list'] as const,
  },
  scans: {
    list: () => ['scans', 'list'] as const,
  },
  printers: {
    list: () => ['printers', 'list'] as const,
  },
  printerStatus: ['printerStatus'] as const,
  scanners: {
    list: () => ['scanners', 'list'] as const,
  },
  settings: ['settings'] as const,
  emailWebhook: ['emailWebhook'] as const,
  dashboardStats: ['dashboardStats'] as const,
  audit: (page: number, action?: string) => ['audit', page, action ?? null] as const,
  users: ['users'] as const,
  cloudProviders: ['cloudProviders'] as const,
  cloudFiles: (providerId: number, key: string) =>
    ['cloudFiles', providerId, key] as const,
  smbShares: ['smbShares'] as const,
  smbBrowse: (shareId: number, path: string) => ['smbBrowse', shareId, path] as const,
  scanProfiles: ['scanProfiles'] as const,
  scannerOptions: ['scannerOptions'] as const,
  webhooks: ['webhooks'] as const,
  apiTokens: ['apiTokens'] as const,
} as const;

/**
 * Print jobs. The cache value keeps the raw `{ jobs, total }` response shape so
 * the WebSocket cache bridge can upsert into it; consumers pick `data.jobs`.
 */
export function useJobs() {
  return useQuery({
    queryKey: queryKeys.jobs.list(),
    queryFn: () => listJobs(),
  });
}

/** Completed scans. Cache value keeps the raw `{ scans, total }` shape. */
export function useScans() {
  return useQuery({
    queryKey: queryKeys.scans.list(),
    queryFn: () => listScans(),
  });
}

export function usePrinters() {
  return useQuery({
    queryKey: queryKeys.printers.list(),
    queryFn: () => listPrinters(),
  });
}

export function usePrinterStatus() {
  return useQuery({
    queryKey: queryKeys.printerStatus,
    queryFn: () => getPrinterStatus(),
  });
}

export function useSettingsQuery() {
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: () => getSettings(),
  });
}

export function useEmailWebhookQuery() {
  return useQuery({
    queryKey: queryKeys.emailWebhook,
    queryFn: () => getEmailWebhookInfo(),
  });
}
