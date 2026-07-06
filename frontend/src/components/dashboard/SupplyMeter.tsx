import { Droplet, TriangleAlert } from 'lucide-react';
import { usePrinterStatus } from '../../api/queries';
import EmptyState from '../common/EmptyState';
import Skeleton from '../common/Skeleton';

interface Marker {
  name: string;
  level: number;
  color: string;
}

type Severity = 'ok' | 'low' | 'critical' | 'unknown';

/** Level thresholds are fixed by the brief: green ≥20, amber <20, red <10.
 * A negative level (CUPS reports −1 / −2 for "unknown") reads as unknown. */
function classify(level: number): Severity {
  if (level < 0) return 'unknown';
  if (level < 10) return 'critical';
  if (level < 20) return 'low';
  return 'ok';
}

// Status colors are reserved for status and never reused as a series color.
const FILL_CLASS: Record<Exclude<Severity, 'unknown'>, string> = {
  ok: 'bg-green-600 dark:bg-green-500',
  low: 'bg-amber-500 dark:bg-amber-400',
  critical: 'bg-red-600 dark:bg-red-500',
};

const LOW_TEXT_CLASS: Record<'low' | 'critical', string> = {
  low: 'text-amber-600 dark:text-amber-400',
  critical: 'text-red-600 dark:text-red-400',
};

const LOW_LABEL: Record<'low' | 'critical', string> = {
  low: 'Low',
  critical: 'Critical',
};

/**
 * Toner/supply levels as thin status meters. The fill carries severity (green /
 * amber / red by level), and a low level always ships an icon + word — never
 * color alone. Unknown levels show a quiet "Unknown" with no fill. No markers at
 * all yields a quiet EmptyState.
 */
export default function SupplyMeter() {
  const { data, isPending } = usePrinterStatus();
  const markers = (data as { markers?: Marker[] } | undefined)?.markers ?? [];

  if (isPending) {
    return <Skeleton variant="row" count={2} />;
  }

  if (markers.length === 0) {
    return <EmptyState icon={Droplet} title="No supply data reported" hint="This printer isn't reporting toner or ink levels." />;
  }

  return (
    <ul className="space-y-3">
      {markers.map((marker, i) => {
        const severity = classify(marker.level);
        const isLow = severity === 'low' || severity === 'critical';
        const known = marker.level >= 0;
        const pct = Math.max(0, Math.min(100, marker.level));

        return (
          <li key={i} className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs text-gray-600 dark:text-gray-400" title={marker.name}>
                {marker.name}
              </span>
              <div className="flex items-center gap-2">
                {isLow && (
                  <span className={`flex items-center gap-1 text-xs font-medium ${LOW_TEXT_CLASS[severity]}`}>
                    <TriangleAlert className="h-3.5 w-3.5" strokeWidth={2} aria-hidden="true" />
                    {LOW_LABEL[severity]}
                  </span>
                )}
                <span
                  className={`font-mono text-xs ${
                    known ? 'text-gray-500 dark:text-gray-400' : 'italic text-gray-400 dark:text-gray-500'
                  }`}
                >
                  {known ? `${marker.level}%` : 'Unknown'}
                </span>
              </div>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
              {known && severity !== 'unknown' && (
                <div
                  data-status={severity}
                  className={`h-full rounded-full ${FILL_CLASS[severity]}`}
                  style={{ width: `${pct}%` }}
                />
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
