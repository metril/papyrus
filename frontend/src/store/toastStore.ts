import { create } from 'zustand';
import type { ToastType } from '../hooks/useToast';

export interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
}

interface ToastStore {
  toasts: ToastItem[];
  show: (message: string, type?: ToastType) => void;
  dismiss: (id: number) => void;
}

let nextId = 0;

// F145: `show`'s auto-hide timer handle used to go nowhere, so `dismiss`
// could only remove the toast from the array — the timer itself kept
// running and fired a (now pointless) `set` call 4s later regardless.
// Tracked outside the zustand state itself (module-level, like `nextId`
// above) since it's bookkeeping for this store's own internals, not data
// any consumer should read.
const timeouts = new Map<number, ReturnType<typeof setTimeout>>();

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  show: (message, type = 'error') => {
    const id = nextId++;
    set((state) => ({ toasts: [...state.toasts, { id, message, type }] }));
    const timeoutId = setTimeout(() => {
      timeouts.delete(id);
      set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
    }, 4000);
    timeouts.set(id, timeoutId);
  },
  dismiss: (id) => {
    const timeoutId = timeouts.get(id);
    if (timeoutId !== undefined) {
      clearTimeout(timeoutId);
      timeouts.delete(id);
    }
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
  },
}));

/**
 * Module-level convenience for firing a toast from non-React code
 * (e.g. React Query's global error callbacks).
 */
export function showToast(message: string, type?: ToastType) {
  useToastStore.getState().show(message, type);
}
