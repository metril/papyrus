import { useQuery } from '@tanstack/react-query';
import { listJobs, getPrinterStatus, getLoadedPaper } from './printer';
import { listScans, listScanProfiles, getScannerOptions } from './scanner';
import { listPrinters } from './printers';
import { useConnectionStore } from '../store/connectionStore';
import { getSettings, getEmailWebhookInfo } from './settings';
import { listScanners } from './scanners';
import { listProviders } from './cloud';
import { listWebhooks, listWebhookEvents } from './webhooks';
import { listApiTokens } from './tokens';
import { listKiosks, listContacts } from './kiosks';
import { lookupDeviceCode, listDeviceRequests, getKioskPinStatus } from './device';
import { getDashboardStats, listUsers } from './admin';

/**
 * Single source of truth for React Query cache keys. Every hook derives its key
 * from here — never inline a key array at a call site.
 */
export const queryKeys = {
  jobs: {
    all: ['jobs'] as const,
    list: () => ['jobs', 'list'] as const,
    // F81: History's own paginated view, independent of the live queue's
    // list() cache. `historyAll` is the shared prefix every `history(page)`
    // key falls under — invalidateQueries matches it against every loaded
    // page without needing to know how many pages exist.
    historyAll: ['jobs', 'history'] as const,
    history: (page: number) => ['jobs', 'history', page] as const,
  },
  scans: {
    list: () => ['scans', 'list'] as const,
    historyAll: ['scans', 'history'] as const,
    history: (page: number) => ['scans', 'history', page] as const,
  },
  printers: {
    list: () => ['printers', 'list'] as const,
  },
  printerStatus: ['printerStatus'] as const,
  loadedPaper: ['loadedPaper'] as const,
  printerDiscovery: ['printerDiscovery'] as const,
  scanners: {
    list: () => ['scanners', 'list'] as const,
    options: () => ['scanners', 'options'] as const,
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
  webhookEvents: ['webhookEvents'] as const,
  apiTokens: ['apiTokens'] as const,
  deviceRequests: ['deviceRequests'] as const,
  kioskPin: ['kioskPin'] as const,
  deviceCode: (code: string) => ['deviceCode', code] as const,
  kiosks: ['kiosks'] as const,
  contacts: ['contacts'] as const,
} as const;

/**
 * Print jobs. The cache value keeps the raw `{ jobs, total }` response shape so
 * the WebSocket cache bridge can upsert into it; consumers pick `data.jobs`.
 *
 * F81: requests the backend's max page size (200, server-capped) rather than
 * its 50-row default — JobQueue needs every held/converting/printing job in
 * view, not just the 50 newest jobs of *any* status. History has its own
 * paginated `useJobs`/`useScans` equivalent below.
 */
export function useJobs() {
  return useQuery({
    queryKey: queryKeys.jobs.list(),
    queryFn: () => listJobs({ limit: 200 }),
  });
}

/** Completed scans. Cache value keeps the raw `{ scans, total }` shape. */
export function useScans() {
  return useQuery({
    queryKey: queryKeys.scans.list(),
    queryFn: () => listScans(),
  });
}

/**
 * Page size for HistoryPage's `queryKeys.jobs/scans.history(page)` queries.
 * A variable, growing number of pages ("Load more") can't be fetched with a
 * fixed set of hook calls, so HistoryPage builds its own `useQueries` array
 * against these keys/params directly rather than a single-page hook here.
 */
export const HISTORY_PAGE_SIZE = 50;

export function usePrinters() {
  return useQuery({
    queryKey: queryKeys.printers.list(),
    queryFn: () => listPrinters(),
  });
}

/**
 * Poll interval for the printer-status query. The realtime bridge invalidates
 * `printerStatus` on every `printer_status` event, so while that socket is up we
 * never poll; when it's down we fall back to a slow 3-minute safety-net refetch.
 */
export function printerStatusRefetchInterval(printersConnected: boolean): number | false {
  return printersConnected ? false : 180_000;
}

export function usePrinterStatus() {
  const printersConnected = useConnectionStore((s) => s.printersConnected);
  return useQuery({
    queryKey: queryKeys.printerStatus,
    queryFn: () => getPrinterStatus(),
    refetchInterval: printerStatusRefetchInterval(printersConnected),
    // The old component swallowed status-fetch errors (rendered nothing); keep
    // that silent behaviour instead of surfacing a global toast.
    meta: { suppressGlobalError: true },
  });
}

/** Paper loaded in the default printer (null = unknown); forms fall back to A4. */
export function useLoadedPaper() {
  return useQuery({
    queryKey: queryKeys.loadedPaper,
    queryFn: () => getLoadedPaper(),
    staleTime: 60_000,
    retry: false,
    meta: { suppressGlobalError: true },
  });
}

/** Saved scan profiles for the scan form. */
export function useScanProfiles() {
  return useQuery({
    queryKey: queryKeys.scanProfiles,
    queryFn: () => listScanProfiles(),
  });
}

export function useSettingsQuery(enabled = true) {
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: () => getSettings(),
    enabled,
  });
}

export function useEmailWebhookQuery() {
  return useQuery({
    queryKey: queryKeys.emailWebhook,
    queryFn: () => getEmailWebhookInfo(),
  });
}

export function useScanners() {
  return useQuery({
    queryKey: queryKeys.scanners.list(),
    queryFn: () => listScanners(),
  });
}

/** Resolutions/modes/sources the default scanner supports. */
export function useScannerOptions() {
  return useQuery({
    queryKey: queryKeys.scanners.options(),
    queryFn: () => getScannerOptions(),
  });
}

export function useCloudProviders() {
  return useQuery({
    queryKey: queryKeys.cloudProviders,
    queryFn: () => listProviders(),
  });
}

export function useWebhooks() {
  return useQuery({
    queryKey: queryKeys.webhooks,
    queryFn: () => listWebhooks(),
  });
}

export function useWebhookEvents() {
  return useQuery({
    queryKey: queryKeys.webhookEvents,
    queryFn: () => listWebhookEvents(),
  });
}

export function useDeviceCode(code: string) {
  return useQuery({
    queryKey: queryKeys.deviceCode(code),
    queryFn: () => lookupDeviceCode(code),
    enabled: code.length > 0,
    retry: false,
  });
}

export function useApiTokens() {
  return useQuery({
    queryKey: queryKeys.apiTokens,
    queryFn: () => listApiTokens(),
  });
}

export function useDashboardStats() {
  return useQuery({
    queryKey: queryKeys.dashboardStats,
    queryFn: () => getDashboardStats(),
    meta: { suppressGlobalError: true },
  });
}

/** Users list for admin management. Kept silent on the global toast — the
 * page renders its own "Admin access required" / failure message. */
export function useUsers() {
  return useQuery({
    queryKey: queryKeys.users,
    queryFn: () => listUsers(),
    meta: { suppressGlobalError: true },
  });
}

export function useKiosks() {
  return useQuery({ queryKey: queryKeys.kiosks, queryFn: () => listKiosks() });
}

/** The shared address book (readable by every signed-in user). */
export function useContacts() {
  return useQuery({ queryKey: queryKeys.contacts, queryFn: () => listContacts() });
}

/** Tap-to-approve sign-in requests waiting on the signed-in user. Kept live by the
 * `notifications` channel in useRealtimeBridge. */
export function useDeviceRequests() {
  return useQuery({
    queryKey: queryKeys.deviceRequests,
    queryFn: () => listDeviceRequests(),
    meta: { suppressGlobalError: true },
  });
}

export function useKioskPinStatus() {
  return useQuery({ queryKey: queryKeys.kioskPin, queryFn: () => getKioskPinStatus() });
}
