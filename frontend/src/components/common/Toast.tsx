import { useEffect, useRef } from 'react';
import type { ToastType } from '../../hooks/useToast';
import { useToastStore, type ToastItem } from '../../store/toastStore';

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  return (
    <>
      {children}
      <div className="fixed bottom-20 md:bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none">
        {toasts.map((t) => (
          <ToastBanner key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
    </>
  );
}

function ToastBanner({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (el) {
      el.style.opacity = '0';
      el.style.transform = 'translateX(1rem)';
      requestAnimationFrame(() => {
        el.style.transition = 'opacity 150ms, transform 150ms';
        el.style.opacity = '1';
        el.style.transform = 'translateX(0)';
      });
    }
  }, []);

  const colors: Record<ToastType, { bg: string; bar: string }> = {
    error: { bg: 'bg-white dark:bg-gray-900 border-gray-100 dark:border-gray-700 text-gray-800 dark:text-gray-200', bar: 'bg-red-500' },
    success: { bg: 'bg-white dark:bg-gray-900 border-gray-100 dark:border-gray-700 text-gray-800 dark:text-gray-200', bar: 'bg-green-500' },
    info: { bg: 'bg-white dark:bg-gray-900 border-gray-100 dark:border-gray-700 text-gray-800 dark:text-gray-200', bar: 'bg-blue-500' },
  };

  const { bg, bar } = colors[toast.type];

  return (
    <div
      ref={ref}
      className={`pointer-events-auto flex items-start gap-3 rounded-lg border pl-0 pr-4 py-3 shadow-xl text-sm overflow-hidden ${bg}`}
    >
      <div className={`w-1 self-stretch rounded-r ${bar}`} />
      <span className="flex-1">{toast.message}</span>
      <button
        onClick={() => onDismiss(toast.id)}
        className="shrink-0 opacity-60 hover:opacity-100 transition-opacity leading-none"
        aria-label="Dismiss"
      >
        ×
      </button>
    </div>
  );
}
