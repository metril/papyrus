import { useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

interface CardProps {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  collapsible?: boolean;
  defaultOpen?: boolean;
}

export default function Card({ title, actions, children, className = '', collapsible = false, defaultOpen = false }: CardProps) {
  const [open, setOpen] = useState(defaultOpen || !collapsible);

  return (
    <div className={`bg-white dark:bg-gray-900 rounded-xl shadow-md shadow-gray-200/50 dark:shadow-none border border-gray-200 dark:border-gray-800 ${className}`}>
      {title && (
        <div
          className={`px-6 py-4 ${collapsible ? 'cursor-pointer select-none hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition-colors' : ''}`}
          onClick={collapsible ? () => setOpen(!open) : undefined}
        >
          <div className="flex items-center justify-between gap-4">
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
            {(actions || collapsible) && (
              <div className="flex items-center gap-2">
                {actions}
                {collapsible && (
                  <ChevronDown
                    className={`w-5 h-5 text-gray-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                )}
              </div>
            )}
          </div>
        </div>
      )}
      {title && open && <hr className="rule-perf mx-6 text-gray-300 dark:text-gray-700" />}
      {open && <div className="p-6">{children}</div>}
    </div>
  );
}
