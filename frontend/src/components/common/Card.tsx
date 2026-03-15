import { useState, type ReactNode } from 'react';

interface CardProps {
  title?: string;
  children: ReactNode;
  className?: string;
  collapsible?: boolean;
  defaultOpen?: boolean;
}

export default function Card({ title, children, className = '', collapsible = false, defaultOpen = false }: CardProps) {
  const [open, setOpen] = useState(defaultOpen || !collapsible);

  return (
    <div className={`bg-white dark:bg-gray-900 rounded-xl shadow-md shadow-gray-200/50 dark:shadow-none border border-gray-100 dark:border-gray-800 ${className}`}>
      {title && (
        <div
          className={`px-6 py-4 ${open ? 'border-b border-gray-100 dark:border-gray-800' : ''} ${collapsible ? 'cursor-pointer select-none hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition-colors' : ''}`}
          onClick={collapsible ? () => setOpen(!open) : undefined}
        >
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
            {collapsible && (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className={`w-5 h-5 text-gray-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            )}
          </div>
        </div>
      )}
      {open && <div className="p-6">{children}</div>}
    </div>
  );
}
