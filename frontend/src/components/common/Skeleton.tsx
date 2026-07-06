type SkeletonVariant = 'text' | 'row' | 'card' | 'thumbnail';

interface SkeletonProps {
  variant?: SkeletonVariant;
  count?: number;
  className?: string;
}

// Paper-toned placeholder shapes; each is a reasonable default for its use
// (a line of text, a list row, a card block, a square preview). Callers can
// widen/narrow via `className`.
const variantClasses: Record<SkeletonVariant, string> = {
  text: 'h-4 rounded w-full',
  row: 'h-14 rounded-lg w-full',
  card: 'h-40 rounded-xl w-full',
  thumbnail: 'h-20 w-20 rounded-lg',
};

function SkeletonBlock({ variant, className }: { variant: SkeletonVariant; className: string }) {
  return (
    <div
      aria-hidden="true"
      className={`relative overflow-hidden bg-gray-200 dark:bg-gray-800 ${variantClasses[variant]} ${className}`}
    >
      <span className="skeleton-shimmer" />
    </div>
  );
}

export default function Skeleton({ variant = 'text', count = 1, className = '' }: SkeletonProps) {
  if (count <= 1) {
    return <SkeletonBlock variant={variant} className={className} />;
  }
  return (
    <div className="space-y-2">
      {Array.from({ length: count }, (_, i) => (
        <SkeletonBlock key={i} variant={variant} className={className} />
      ))}
    </div>
  );
}
