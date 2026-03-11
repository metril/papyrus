interface StatusBadgeProps {
  status: string;
}

const statusColors: Record<string, string> = {
  held: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300',
  converting: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',
  printing: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',
  scanning: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',
  completed: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-400',
  failed: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-400',
  cancelled: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300',
  deleted: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300',
};

export default function StatusBadge({ status }: StatusBadgeProps) {
  const color = statusColors[status] || 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300';
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${color}`}>
      {status}
    </span>
  );
}
