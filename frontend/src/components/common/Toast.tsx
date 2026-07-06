import { useEffect, useRef } from 'react';
import { CircleCheck, CircleX, Info, type LucideIcon } from 'lucide-react';
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

  const iconStyles: Record<ToastType, { Icon: LucideIcon; iconClass: string; barClass: string }> = {
    error: { Icon: CircleX, iconClass: 'text-red-500 dark:text-red-400', barClass: 'bg-red-500' },
    success: { Icon: CircleCheck, iconClass: 'text-green-500 dark:text-green-400', barClass: 'bg-green-500' },
    info: { Icon: Info, iconClass: 'text-ink-500 dark:text-ink-400', barClass: 'bg-ink-500' },
  };

  const { Icon, iconClass, barClass } = iconStyles[toast.type];

  return (
    <div
      ref={ref}
      className="pointer-events-auto flex items-start gap-3 rounded-lg border border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 pl-0 pr-4 py-3 shadow-xl text-sm overflow-hidden"
    >
      <div className={`w-1 self-stretch rounded-r ${barClass}`} />
      <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${iconClass}`} strokeWidth={1.75} aria-hidden="true" />
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
