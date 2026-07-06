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

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  show: (message, type = 'error') => {
    const id = nextId++;
    set((state) => ({ toasts: [...state.toasts, { id, message, type }] }));
    setTimeout(() => {
      set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
    }, 4000);
  },
  dismiss: (id) =>
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
}));

/**
 * Module-level convenience for firing a toast from non-React code
 * (e.g. React Query's global error callbacks).
 */
export function showToast(message: string, type?: ToastType) {
  useToastStore.getState().show(message, type);
}
