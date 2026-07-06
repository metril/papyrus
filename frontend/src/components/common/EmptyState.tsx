import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  hint?: string;
  action?: ReactNode;
}

// Paper-sheet motif: an icon in a rounded rect, a torn-perforation rule under
// the title, then an optional hint and action inviting the next step.
export default function EmptyState({ icon: Icon, title, hint, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      {Icon && (
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-xl border border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-800/60">
          <Icon className="h-6 w-6 text-gray-400 dark:text-gray-500" strokeWidth={1.75} aria-hidden="true" />
        </div>
      )}
      <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
      <hr className="rule-perf my-3 w-16 text-gray-300 dark:text-gray-700" />
      {hint && <p className="max-w-sm text-sm text-gray-500 dark:text-gray-400">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
