import { useState, useEffect } from 'react';
import Card from '../common/Card';
import Button from '../common/Button';
import { useToast } from '../../hooks/useToast';
import api from '../../api/client';

interface WebhookItem {
  id: number;
  name: string;
  url: string;
  events: string[];
  enabled: boolean;
  created_at: string;
}

export default function WebhooksCard() {
  const toast = useToast();
  const [hooks, setHooks] = useState<WebhookItem[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ name: '', url: '', secret: '', events: [] as string[] });

  const load = () => {
    api.get('/webhooks').then(({ data }) => setHooks(data)).catch(() => {});
    api.get('/webhooks/events').then(({ data }) => setEvents(data)).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    if (!form.name || !form.url || form.events.length === 0) return;
    try {
      await api.post('/webhooks', form);
      setForm({ name: '', url: '', secret: '', events: [] });
      setShowAdd(false);
      load();
    } catch { toast.show('Failed to create webhook', 'error'); }
  };

  const toggleEnabled = async (hook: WebhookItem) => {
    try {
      await api.put(`/webhooks/${hook.id}`, { ...hook, enabled: !hook.enabled });
      load();
    } catch { toast.show('Failed to update webhook', 'error'); }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.delete(`/webhooks/${id}`);
      load();
    } catch { toast.show('Failed to delete webhook', 'error'); }
  };

  const toggleEvent = (evt: string) => {
    setForm((f) => ({
      ...f,
      events: f.events.includes(evt) ? f.events.filter((e) => e !== evt) : [...f.events, evt],
    }));
  };

  return (
    <Card title="Webhooks" collapsible>
      <div className="space-y-4">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Send HTTP POST notifications when events occur. Payloads are signed with HMAC-SHA256 if a secret is set.
        </p>
        {hooks.length === 0 && !showAdd && (
          <p className="text-gray-500 text-sm">No webhooks configured.</p>
        )}
        {hooks.map((hook) => (
          <div key={hook.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{hook.name}</div>
              <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{hook.url}</div>
              <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">{hook.events.join(', ')}</div>
            </div>
            <div className="flex items-center gap-2 ml-3">
              <button
                onClick={() => toggleEnabled(hook)}
                className={`px-2 py-1 text-xs rounded ${hook.enabled ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}`}
              >{hook.enabled ? 'On' : 'Off'}</button>
              <Button size="sm" variant="danger" onClick={() => handleDelete(hook.id)}>Delete</Button>
            </div>
          </div>
        ))}
        {showAdd ? (
          <div className="space-y-3 p-3 border border-gray-200 dark:border-gray-700 rounded-lg">
            <input
              type="text"
              placeholder="Webhook name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
            />
            <input
              type="url"
              placeholder="https://example.com/webhook"
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
            />
            <input
              type="text"
              placeholder="Signing secret (optional)"
              value={form.secret}
              onChange={(e) => setForm({ ...form, secret: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
            />
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Events</label>
              <div className="flex flex-wrap gap-2">
                {events.map((evt) => (
                  <button
                    key={evt}
                    onClick={() => toggleEvent(evt)}
                    className={`px-2 py-1 text-xs rounded border ${form.events.includes(evt) ? 'bg-blue-50 border-blue-300 text-blue-700 dark:bg-blue-950 dark:border-blue-700 dark:text-blue-300' : 'border-gray-300 text-gray-500 dark:border-gray-600 dark:text-gray-400'}`}
                  >{evt}</button>
                ))}
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="secondary" onClick={() => setShowAdd(false)}>Cancel</Button>
              <Button size="sm" onClick={handleCreate}>Create</Button>
            </div>
          </div>
        ) : (
          <Button size="sm" onClick={() => setShowAdd(true)}>Add Webhook</Button>
        )}
      </div>
    </Card>
  );
}
