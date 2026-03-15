import { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import api from '../api/client';

interface AuditEntry {
  id: number;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  user_id: string | null;
  source: string;
  ip_address: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [page, setPage] = useState(0);
  const [actionFilter, setActionFilter] = useState('');
  const pageSize = 50;

  useEffect(() => {
    setLoading(true);
    setError('');
    const params: Record<string, string | number> = { limit: pageSize, offset: page * pageSize };
    if (actionFilter) params.action = actionFilter;

    api.get('/admin/audit', { params })
      .then(({ data }) => {
        setEntries(data.entries);
        setTotal(data.total);
      })
      .catch((err: unknown) => {
        setEntries([]);
        setTotal(0);
        const status = (err as { response?: { status?: number } })?.response?.status;
        setError(status === 403 ? 'Admin access required' : 'Failed to load audit log');
      })
      .finally(() => setLoading(false));
  }, [page, actionFilter]);

  const actions = [
    '', 'print.release', 'print.cancel', 'print.delete', 'print.upload',
    'scan.complete', 'scan.delete', 'scan.ocr',
    'cloud.upload', 'email.send', 'settings.update',
  ];

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Audit Log</h2>

      <Card>
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <select
              value={actionFilter}
              onChange={(e) => { setActionFilter(e.target.value); setPage(0); }}
              className="rounded-lg border-gray-300 dark:border-gray-600 text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
            >
              <option value="">All actions</option>
              {actions.filter(Boolean).map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
            <span className="text-sm text-gray-500 dark:text-gray-400">
              {total} entries
            </span>
          </div>

          {loading ? (
            <p className="text-gray-500 text-sm">Loading...</p>
          ) : error ? (
            <p className="text-red-600 dark:text-red-400 text-sm">{error}</p>
          ) : entries.length === 0 ? (
            <p className="text-gray-500 text-sm">No audit entries found.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 dark:border-gray-700 text-left">
                    <th className="py-2 pr-4 font-medium text-gray-600 dark:text-gray-400">Time</th>
                    <th className="py-2 pr-4 font-medium text-gray-600 dark:text-gray-400">Action</th>
                    <th className="py-2 pr-4 font-medium text-gray-600 dark:text-gray-400">Entity</th>
                    <th className="py-2 pr-4 font-medium text-gray-600 dark:text-gray-400">Source</th>
                    <th className="py-2 pr-4 font-medium text-gray-600 dark:text-gray-400">IP</th>
                    <th className="py-2 font-medium text-gray-600 dark:text-gray-400">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => (
                    <tr key={e.id} className="border-b border-gray-100 dark:border-gray-800">
                      <td className="py-2 pr-4 text-gray-600 dark:text-gray-400 whitespace-nowrap">
                        {new Date(e.created_at).toLocaleString()}
                      </td>
                      <td className="py-2 pr-4">
                        <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200">
                          {e.action}
                        </span>
                      </td>
                      <td className="py-2 pr-4 text-gray-700 dark:text-gray-300">
                        {e.entity_type && <span>{e.entity_type}</span>}
                        {e.entity_id && <span className="text-gray-500 ml-1">#{e.entity_id}</span>}
                      </td>
                      <td className="py-2 pr-4 text-gray-600 dark:text-gray-400">{e.source}</td>
                      <td className="py-2 pr-4 text-gray-500 dark:text-gray-500 font-mono text-xs">{e.ip_address || '-'}</td>
                      <td className="py-2 text-gray-500 dark:text-gray-400 text-xs max-w-xs truncate">
                        {e.detail ? JSON.stringify(e.detail) : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {total > pageSize && (
            <div className="flex items-center gap-2 justify-center">
              <Button size="sm" variant="ghost" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
                Previous
              </Button>
              <span className="text-sm text-gray-500 dark:text-gray-400">
                Page {page + 1} of {Math.ceil(total / pageSize)}
              </span>
              <Button size="sm" variant="ghost" onClick={() => setPage((p) => p + 1)} disabled={(page + 1) * pageSize >= total}>
                Next
              </Button>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
