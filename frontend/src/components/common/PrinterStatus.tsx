import { useState, useEffect } from 'react';
import api from '../../api/client';

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
}

const stateLabels: Record<number, string> = {
  3: 'Idle',
  4: 'Printing',
  5: 'Stopped',
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
  const [status, setStatus] = useState<PrinterStatusData | null>(null);

  useEffect(() => {
    const fetch = () => {
      api.get('/printer/status').then(({ data }) => setStatus(data)).catch(() => {});
    };
    fetch();
    const interval = setInterval(fetch, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!status) return null;

  const stateColor = status.state === 3 ? 'text-green-600 dark:text-green-400'
    : status.state === 4 ? 'text-blue-600 dark:text-blue-400'
    : 'text-red-600 dark:text-red-400';

  const hasIssues = status.state_reasons.some((r) => r !== 'none');

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${status.state === 3 ? 'bg-green-500' : status.state === 4 ? 'bg-blue-500 animate-pulse' : 'bg-red-500'}`} />
          <span className={`text-sm font-medium ${stateColor}`}>
            {stateLabels[status.state] || 'Unknown'}
          </span>
          {status.state_message && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              &mdash; {status.state_message}
            </span>
          )}
        </div>
        {!status.accepting_jobs && (
          <span className="text-xs text-red-500 font-medium">Not accepting jobs</span>
        )}
      </div>

      {/* Marker/toner levels */}
      {status.markers.length > 0 && (
        <div className="space-y-1.5">
          {status.markers.map((m, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="text-xs text-gray-600 dark:text-gray-400 w-20 truncate" title={m.name}>
                {m.name}
              </span>
              <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded-full h-2 overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.max(0, Math.min(100, m.level))}%`,
                    backgroundColor: markerColor(m.color),
                  }}
                />
              </div>
              <span className="text-xs text-gray-500 dark:text-gray-400 w-8 text-right">
                {m.level >= 0 ? `${m.level}%` : '?'}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* State reasons / warnings */}
      {hasIssues && (
        <div className="text-xs text-yellow-600 dark:text-yellow-400">
          {status.state_reasons.filter((r) => r !== 'none').join(', ')}
        </div>
      )}
    </div>
  );
}
