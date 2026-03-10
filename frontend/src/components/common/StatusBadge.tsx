interface StatusBadgeProps {
  status: string;
}

const statusColors: Record<string, string> = {
  held: 'bg-yellow-100 text-yellow-800',
  converting: 'bg-blue-100 text-blue-800',
  printing: 'bg-blue-100 text-blue-800',
  scanning: 'bg-blue-100 text-blue-800',
  completed: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-800',
  cancelled: 'bg-gray-100 text-gray-800',
  deleted: 'bg-gray-100 text-gray-800',
};

export default function StatusBadge({ status }: StatusBadgeProps) {
  const color = statusColors[status] || 'bg-gray-100 text-gray-800';
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${color}`}>
      {status}
    </span>
  );
}
