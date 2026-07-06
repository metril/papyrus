import { useQuery } from '@tanstack/react-query';
import Button from '../common/Button';
import { discoverPrinters } from '../../api/printers';
import { queryKeys } from '../../api/queries';
import type { DiscoveredPrinter } from '../../types';

const protocolColors: Record<string, string> = {
  ipp: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  ipps: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400',
  lpd: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400',
};

interface PrinterDiscoveryProps {
  onSelect: (device: DiscoveredPrinter) => void;
}

export default function PrinterDiscovery({ onSelect }: PrinterDiscoveryProps) {
  // staleTime/gcTime 0 + no window-focus refetch: every mount is a fresh scan
  // (mirrors the old mount-effect), and StrictMode's double-invoke of the
  // effect now dedupes into a single in-flight request instead of firing the
  // ~4s discover call twice. `error` keeps its previous data (the last good
  // device list) so a transient rescan failure doesn't wipe good results.
  const { data, isFetching, isError, refetch } = useQuery({
    queryKey: queryKeys.printerDiscovery,
    queryFn: discoverPrinters,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    meta: { suppressGlobalError: true },
  });
  const devices = data ?? [];

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500 dark:text-gray-400">
          {isFetching
            ? 'Scanning network…'
            : `${devices.length} device${devices.length === 1 ? '' : 's'} found`}
        </span>
        <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? 'Scanning…' : 'Rescan'}
        </Button>
      </div>

      {!isFetching && isError && (
        <p className="text-sm text-red-600 dark:text-red-400">
          Scan failed — try again.
        </p>
      )}

      {!isFetching && !isError && devices.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No printers found — your network may block mDNS; use the IP Address tab.
        </p>
      )}

      {devices.length > 0 && (
        <div className="space-y-1.5">
          {devices.map((device) => (
            <DiscoveredPrinterRow
              key={device.uuid ?? `${device.ip}:${device.port}`}
              device={device}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function DiscoveredPrinterRow({
  device,
  onSelect,
}: {
  device: DiscoveredPrinter;
  onSelect: (device: DiscoveredPrinter) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2 p-2 rounded-lg border border-gray-200 dark:border-gray-700">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">
            {device.make_model || device.name}
          </span>
          {device.protocols.map((proto) => (
            <span
              key={proto}
              className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${protocolColors[proto] || 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}
            >
              {proto}
            </span>
          ))}
        </div>
        <div className="text-xs text-gray-500 dark:text-gray-400 truncate">
          {device.ip}
          {device.location ? ` · ${device.location}` : ''}
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {device.already_configured && (
          <span className="text-xs px-1.5 py-0.5 rounded-full font-medium bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400">
            Already added
          </span>
        )}
        <Button
          size="sm"
          variant="secondary"
          onClick={() => onSelect(device)}
          disabled={device.already_configured}
        >
          Add
        </Button>
      </div>
    </div>
  );
}
