interface StatusBadgeProps {
  status: string;
}

const statusColors: Record<string, { badge: string; dot: string }> = {
  held: { badge: 'bg-yellow-50 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300', dot: 'bg-yellow-500' },
  converting: { badge: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300', dot: 'bg-blue-500 animate-pulse' },
  printing: { badge: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300', dot: 'bg-blue-500 animate-pulse' },
  scanning: { badge: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300', dot: 'bg-blue-500 animate-pulse' },
  completed: { badge: 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400', dot: 'bg-green-500' },
  failed: { badge: 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400', dot: 'bg-red-500' },
  cancelled: { badge: 'bg-gray-50 text-gray-600 dark:bg-gray-800 dark:text-gray-300', dot: 'bg-gray-400' },
  deleted: { badge: 'bg-gray-50 text-gray-600 dark:bg-gray-800 dark:text-gray-300', dot: 'bg-gray-400' },
};

const fallback = { badge: 'bg-gray-50 text-gray-600 dark:bg-gray-800 dark:text-gray-300', dot: 'bg-gray-400' };

export default function StatusBadge({ status }: StatusBadgeProps) {
  const { badge, dot } = statusColors[status] || fallback;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium ${badge}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      {status}
    </span>
  );
}
