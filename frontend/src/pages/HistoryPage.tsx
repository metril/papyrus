import { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import StatusBadge from '../components/common/StatusBadge';
import api from '../api/client';
import type { PrintJob, ScanJob } from '../types';

type Tab = 'all' | 'print' | 'scan';

interface HistoryItem {
  type: 'print' | 'scan';
  id: string;
  label: string;
  status: string;
  time: string;
  detail: string;
}

export default function HistoryPage() {
  const [tab, setTab] = useState<Tab>('all');
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const [jobsRes, scansRes] = await Promise.all([
          api.get('/jobs'),
          api.get('/scanner/scans'),
        ]);

        const printItems: HistoryItem[] = (jobsRes.data.jobs as PrintJob[]).map((j) => ({
          type: 'print',
          id: `print-${j.id}`,
          label: j.filename,
          status: j.status,
          time: j.created_at,
          detail: `${j.copies} cop${j.copies > 1 ? 'ies' : 'y'} \u00b7 ${j.media}${j.duplex ? ' \u00b7 Duplex' : ''}`,
        }));

        const scanItems: HistoryItem[] = (scansRes.data.scans as ScanJob[]).map((s) => ({
          type: 'scan',
          id: `scan-${s.scan_id}`,
          label: `${s.format.toUpperCase()} ${s.resolution} DPI`,
          status: s.status,
          time: s.created_at,
          detail: `${s.mode} \u00b7 ${s.source}${s.page_count > 1 ? ` \u00b7 ${s.page_count} pages` : ''}`,
        }));

        const all = [...printItems, ...scanItems].sort(
          (a, b) => new Date(b.time).getTime() - new Date(a.time).getTime()
        );
        setItems(all);
      } catch {
        // Ignore errors
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  const filtered = tab === 'all' ? items : items.filter((i) => i.type === tab);

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">History</h2>

      <div className="flex gap-2">
        {(['all', 'print', 'scan'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === t ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
            }`}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      <Card>
        {loading ? (
          <p className="text-gray-500 text-sm">Loading history...</p>
        ) : filtered.length === 0 ? (
          <p className="text-gray-500 text-sm">No history yet.</p>
        ) : (
          <div className="space-y-3">
            {filtered.map((item) => (
              <div
                key={item.id}
                className="flex items-center justify-between p-3 rounded-lg border border-gray-100 dark:border-gray-800"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold uppercase text-gray-400 dark:text-gray-500">
                      {item.type}
                    </span>
                    <span className="text-sm text-gray-900 dark:text-gray-100 truncate">{item.label}</span>
                    <StatusBadge status={item.status} />
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    {item.detail} &middot; {new Date(item.time).toLocaleString()}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
