import { usePrinterStatus } from '../../api/queries';

interface Marker {
  name: string;
  level: number;
  color: string;
}

interface PrinterStatusData {
  state: number;
  state_message: string;
  accepting_jobs: boolean;
  markers: Marker[];
  state_reasons: string[];
  // Forward-compatible: `/printer/status` (schemas.PrinterStatus) does not
  // send these today, so they are currently always absent. Rendered only
  // when present rather than fetched via a new query/field.
  display_name?: string;
  queue_count?: number;
  media?: string;
}

const stateLabels: Record<number, string> = {
  3: 'Idle',
  4: 'Printing',
  5: 'Stopped',
};

type StateFamily = 'success' | 'active' | 'error';

const stateFamily: Record<number, StateFamily> = {
  3: 'success', // idle
  4: 'active', // printing
  5: 'error', // stopped
};

// Same dot-color language as StatusBadge's family map (success/active/error);
// duplicated here because this component keys off CUPS's numeric `state`
// (3/4/5), not the string status vocabulary StatusBadge maps.
const dotClasses: Record<StateFamily, string> = {
  success: 'text-green-500 dark:text-green-400',
  active: 'text-ink-500 dark:text-ink-400',
  error: 'text-red-500 dark:text-red-400',
};

function markerColor(color: string): string {
  // CUPS marker colors are like "#000000" or "none" or "cyan" etc.
  if (color.startsWith('#')) return color;
  const map: Record<string, string> = {
    black: '#000000',
    cyan: '#00BFFF',
    magenta: '#FF00FF',
    yellow: '#FFD700',
    none: '#888888',
  };
  return map[color.toLowerCase()] || '#888888';
}

export default function PrinterStatus() {
  // The realtime bridge invalidates `printerStatus` on every `printer_status`
  // event (and on reconnect); `usePrinterStatus` also falls back to a slow poll
  // while the printers socket is down. `/printer/status` returns the fuller CUPS
  // blob (markers + state_reasons) than the shared PrinterStatus type models.
  const { data } = usePrinterStatus();
  const status = data as PrinterStatusData | undefined;

  if (!status) return null;

  const family = stateFamily[status.state] ?? 'error';
  const label = stateLabels[status.state] || 'Unknown';
  const hasIssues = status.state_reasons.some((r) => r !== 'none');

  return (
    <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          {status.display_name || 'Printer'}
        </h3>
        {!status.accepting_jobs && (
          <span className="text-xs font-medium text-red-500 dark:text-red-400">Not accepting jobs</span>
        )}
      </div>

      {/* Front-panel readouts: LED + mono value, like the machine's own display */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className={`led ${dotClasses[family]} ${family === 'active' ? 'led-pulse' : ''}`} />
          <span className="font-mono text-sm text-gray-700 dark:text-gray-300">{label}</span>
          {status.state_message && (
            <span className="text-xs text-gray-500 dark:text-gray-400">&mdash; {status.state_message}</span>
          )}
        </div>
        {typeof status.queue_count === 'number' && (
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-gray-500 dark:text-gray-400">Queue</span>
            <span className="font-mono text-sm text-gray-700 dark:text-gray-300">{status.queue_count}</span>
          </div>
        )}
        {status.media && (
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-gray-500 dark:text-gray-400">Media</span>
            <span className="font-mono text-sm text-gray-700 dark:text-gray-300">{status.media}</span>
          </div>
        )}
      </div>

      {/* Marker/toner levels: thin ink meter bars with mono percentages */}
      {status.markers.length > 0 && (
        <div className="space-y-1.5">
          {status.markers.map((m, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-20 shrink-0 truncate text-xs text-gray-600 dark:text-gray-400" title={m.name}>
                {m.name}
              </span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.max(0, Math.min(100, m.level))}%`,
                    backgroundColor: markerColor(m.color),
                  }}
                />
              </div>
              <span className="w-10 shrink-0 text-right font-mono text-xs text-gray-500 dark:text-gray-400">
                {m.level >= 0 ? `${m.level}%` : '—'}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* State reasons / warnings */}
      {hasIssues && (
        <div className="text-xs text-amber-600 dark:text-amber-400">
          {status.state_reasons.filter((r) => r !== 'none').join(', ')}
        </div>
      )}
    </div>
  );
}
