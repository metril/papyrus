import { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import api from '../api/client';

interface DailyCount {
  day: string;
  count: number;
}

interface Stats {
  print_counts: Record<string, number>;
  scan_counts: Record<string, number>;
  daily_prints: DailyCount[];
  daily_scans: DailyCount[];
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/admin/stats')
      .then(({ data }) => setStats(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Dashboard</h2>
        <p className="text-gray-500 text-sm">Loading...</p>
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="space-y-6">
        <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Dashboard</h2>
        <p className="text-gray-500 text-sm">Failed to load stats. Admin access required.</p>
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
        <Card>
          <div className="text-center">
            <div className="text-3xl font-bold text-blue-600 dark:text-blue-400">{totalPrints}</div>
            <div className="text-sm text-gray-500 dark:text-gray-400">Total Prints</div>
          </div>
        </Card>
        <Card>
          <div className="text-center">
            <div className="text-3xl font-bold text-green-600 dark:text-green-400">{totalScans}</div>
            <div className="text-sm text-gray-500 dark:text-gray-400">Total Scans</div>
          </div>
        </Card>
        <Card>
          <div className="text-center">
            <div className="text-3xl font-bold text-yellow-600 dark:text-yellow-400">{stats.print_counts.held || 0}</div>
            <div className="text-sm text-gray-500 dark:text-gray-400">Held Jobs</div>
          </div>
        </Card>
        <Card>
          <div className="text-center">
            <div className="text-3xl font-bold text-red-600 dark:text-red-400">{stats.print_counts.failed || 0}</div>
            <div className="text-sm text-gray-500 dark:text-gray-400">Failed</div>
          </div>
        </Card>
      </div>

      {/* Print status breakdown */}
      <Card title="Print Jobs by Status">
        <div className="flex flex-wrap gap-4">
          {Object.entries(stats.print_counts).map(([status, count]) => (
            <div key={status} className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300 capitalize">{status}:</span>
              <span className="text-sm text-gray-900 dark:text-gray-100">{count}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Scan status breakdown */}
      <Card title="Scan Jobs by Status">
        <div className="flex flex-wrap gap-4">
          {Object.entries(stats.scan_counts).map(([status, count]) => (
            <div key={status} className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-700 dark:text-gray-300 capitalize">{status}:</span>
              <span className="text-sm text-gray-900 dark:text-gray-100">{count}</span>
            </div>
          ))}
        </div>
      </Card>

      {/* Daily activity */}
      <Card title="Daily Activity (Last 30 Days)">
        <div className="space-y-4">
          {stats.daily_prints.length === 0 && stats.daily_scans.length === 0 ? (
            <p className="text-gray-500 text-sm">No activity in the last 30 days.</p>
          ) : (
            <>
              <div>
                <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Prints</h4>
                <div className="flex items-end gap-1 h-24">
                  {stats.daily_prints.map((d) => (
                    <div key={d.day} className="flex-1 flex flex-col items-center" title={`${d.day}: ${d.count}`}>
                      <div
                        className="w-full bg-blue-500 dark:bg-blue-400 rounded-t"
                        style={{ height: `${(d.count / maxDaily) * 100}%`, minHeight: d.count > 0 ? '4px' : '0' }}
                      />
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Scans</h4>
                <div className="flex items-end gap-1 h-24">
                  {stats.daily_scans.map((d) => (
                    <div key={d.day} className="flex-1 flex flex-col items-center" title={`${d.day}: ${d.count}`}>
                      <div
                        className="w-full bg-green-500 dark:bg-green-400 rounded-t"
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
