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

  setProgress: (progress: number) => set({ progress }),
  setActiveScan: (scan: ScanJob | null) => set({ activeScan: scan }),

  deleteScan: async (scanId: string) => {
    await api.delete(`/scanner/scans/${scanId}`);
    set({ scans: get().scans.filter((s) => s.scan_id !== scanId) });
  },
}));
