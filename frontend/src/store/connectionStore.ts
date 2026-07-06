import { create } from 'zustand';

/**
 * Realtime connection state for the three WS channels the bridge owns. The
 * bridge (`useRealtimeBridge`) writes these; later consumers (e.g. PrinterStatus
 * in Task 8) read them to decide whether a fallback poll is needed when a socket
 * is down. This is pure client state, so it lives in Zustand, not the Query cache.
 */
interface ConnectionState {
  jobsConnected: boolean;
  scansConnected: boolean;
  printersConnected: boolean;
  setJobsConnected: (connected: boolean) => void;
  setScansConnected: (connected: boolean) => void;
  setPrintersConnected: (connected: boolean) => void;
}

export const useConnectionStore = create<ConnectionState>((set) => ({
  jobsConnected: false,
  scansConnected: false,
  printersConnected: false,
  setJobsConnected: (jobsConnected) => set({ jobsConnected }),
  setScansConnected: (scansConnected) => set({ scansConnected }),
  setPrintersConnected: (printersConnected) => set({ printersConnected }),
}));
