import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

type ToastType = 'error' | 'success' | 'info';

interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
}

interface ToastContextValue {
  show: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

let nextId = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const show = useCallback((message: string, type: ToastType = 'error') => {
    const id = nextId++;
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="fixed bottom-20 md:bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full pointer-events-none">
        {toasts.map((t) => (
          <ToastBanner key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
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

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used inside ToastProvider');
  return ctx;
}
