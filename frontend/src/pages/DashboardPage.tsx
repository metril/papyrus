import Card from '../components/common/Card';
import Skeleton from '../components/common/Skeleton';
import ErrorState from '../components/common/ErrorState';
import { useDashboardStats } from '../api/queries';

interface StatTileProps {
  label: string;
  value: number;
  valueClassName: string;
}

function StatTile({ label, value, valueClassName }: StatTileProps) {
  return (
    <Card>
      <div className="text-center">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
          {label}
        </div>
        <div className={`mt-1.5 font-mono text-3xl font-semibold ${valueClassName}`}>{value}</div>
      </div>
    </Card>
  );
}

export default function DashboardPage() {
  const { data: stats, isPending, isError, refetch } = useDashboardStats();

  if (isPending) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Dashboard</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} variant="card" />
          ))}
        </div>
        <Skeleton variant="card" count={3} />
      </div>
    );
  }

  if (isError || !stats) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Dashboard</h2>
        <ErrorState title="Failed to load stats" detail="You may need admin access, or the server may be unavailable." onRetry={() => refetch()} />
      </div>
    );
  }

  const totalPrints = Object.values(stats.print_counts).reduce((a, b) => a + b, 0);
  const totalScans = Object.values(stats.scan_counts).reduce((a, b) => a + b, 0);

  // Simple bar chart using divs
  const maxDaily = Math.max(
    ...stats.daily_prints.map((d) => d.count),
    ...stats.daily_scans.map((d) => d.count),
    1,
  );

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Dashboard</h2>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatTile label="Total Prints" value={totalPrints} valueClassName="text-ink-600 dark:text-ink-400" />
        <StatTile label="Total Scans" value={totalScans} valueClassName="text-green-600 dark:text-green-400" />
        <StatTile label="Held Jobs" value={stats.print_counts.held || 0} valueClassName="text-amber-600 dark:text-amber-400" />
        <StatTile label="Failed" value={stats.print_counts.failed || 0} valueClassName="text-red-600 dark:text-red-400" />
      </div>

      {/* Print status breakdown */}
      <Card title="Print Jobs by Status">
        <div className="flex flex-wrap gap-4">
          {Object.entries(stats.print_counts).map(([status, count]) => (
            <div key={status} className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-500 dark:text-gray-400 capitalize">{status}:</span>
              <span className="font-mono text-sm text-gray-900 dark:text-gray-100">{count}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Scan status breakdown */}
      <Card title="Scan Jobs by Status">
        <div className="flex flex-wrap gap-4">
          {Object.entries(stats.scan_counts).map(([status, count]) => (
            <div key={status} className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-500 dark:text-gray-400 capitalize">{status}:</span>
              <span className="font-mono text-sm text-gray-900 dark:text-gray-100">{count}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Daily activity */}
      <Card title="Daily Activity (Last 30 Days)">
        <div className="space-y-4">
          {stats.daily_prints.length === 0 && stats.daily_scans.length === 0 ? (
            <p className="text-gray-500 dark:text-gray-400 text-sm">No activity in the last 30 days.</p>
          ) : (
            <>
              <div>
                <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 mb-2">Prints</h4>
                <div className="flex items-end gap-1 h-24">
                  {stats.daily_prints.map((d) => (
                    <div
                      key={d.day}
                      className="flex h-full flex-1 flex-col justify-end overflow-hidden rounded-t bg-gray-100 dark:bg-gray-800"
                      title={`${d.day}: ${d.count}`}
                    >
                      <div
                        className="w-full bg-ink-600 dark:bg-ink-400 rounded-t"
                        style={{ height: `${(d.count / maxDaily) * 100}%`, minHeight: d.count > 0 ? '4px' : '0' }}
                      />
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <h4 className="text-sm font-medium text-gray-500 dark:text-gray-400 mb-2">Scans</h4>
                <div className="flex items-end gap-1 h-24">
                  {stats.daily_scans.map((d) => (
                    <div
                      key={d.day}
                      className="flex h-full flex-1 flex-col justify-end overflow-hidden rounded-t bg-gray-100 dark:bg-gray-800"
                      title={`${d.day}: ${d.count}`}
                    >
                      <div
                        className="w-full bg-ink-600 dark:bg-ink-400 rounded-t"
                        style={{ height: `${(d.count / maxDaily) * 100}%`, minHeight: d.count > 0 ? '4px' : '0' }}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
