import { useToastStore } from '../store/toastStore';

export type ToastType = 'error' | 'success' | 'info';

export interface ToastContextValue {
  show: (message: string, type?: ToastType) => void;
}

export function useToast(): ToastContextValue {
  const show = useToastStore((s) => s.show);
  return { show };
}
