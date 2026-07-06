import { create } from 'zustand';
import type { ScanJob } from '../types';

/**
 * Client-only scan UI state. The scan LIST is server state and lives in the
 * Query cache (kept live by the WS bridge); this store holds just the transient
 * bits driven by the per-scan progress socket in ScanForm.
 */
interface ScanState {
  activeScan: ScanJob | null;
  progress: number;
  setActiveScan: (scan: ScanJob | null) => void;
  setProgress: (progress: number) => void;
}

export const useScanStore = create<ScanState>((set) => ({
  activeScan: null,
  progress: 0,
  setActiveScan: (scan) => set({ activeScan: scan }),
  setProgress: (progress) => set({ progress }),
}));
