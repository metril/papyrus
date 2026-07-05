import { create } from 'zustand';
import type { ScanJob } from '../types';
import api from '../api/client';

interface ScanState {
  scans: ScanJob[];
  activeScan: ScanJob | null;
  progress: number;
  loading: boolean;
  error: string | null;
  fetchScans: () => Promise<void>;
  updateScan: (scan: ScanJob) => void;
  upsertScan: (scan: ScanJob) => void;
  removeScan: (scanId: string) => void;
  setProgress: (progress: number) => void;
  setActiveScan: (scan: ScanJob | null) => void;
  deleteScan: (id: string) => Promise<void>;
}

export const useScanStore = create<ScanState>((set, get) => ({
  scans: [],
  activeScan: null,
  progress: 0,
  loading: false,
  error: null,

  fetchScans: async () => {
    try {
      set({ loading: true, error: null });
      const response = await api.get('/scanner/scans');
      set({ scans: response.data.scans, loading: false });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to fetch scans';
      set({ error: message, loading: false });
    }
  },

  updateScan: (updatedScan: ScanJob) => {
    set({
      scans: get().scans.map((s) => (s.scan_id === updatedScan.scan_id ? updatedScan : s)),
    });
  },

  // Apply a full scan object from a WS event: replace an existing row (matched
  // on scan_id) in place, or insert an unseen scan at the top (newest-first).
  upsertScan: (incoming: ScanJob) => {
    const scans = get().scans;
    const exists = scans.some((s) => s.scan_id === incoming.scan_id);
    set({
      scans: exists
        ? scans.map((s) => (s.scan_id === incoming.scan_id ? incoming : s))
        : [incoming, ...scans],
    });
  },

  removeScan: (scanId: string) => {
    set({ scans: get().scans.filter((s) => s.scan_id !== scanId) });
  },

  setProgress: (progress: number) => set({ progress }),
  setActiveScan: (scan: ScanJob | null) => set({ activeScan: scan }),

  deleteScan: async (scanId: string) => {
    await api.delete(`/scanner/scans/${scanId}`);
    set({ scans: get().scans.filter((s) => s.scan_id !== scanId) });
  },
}));
