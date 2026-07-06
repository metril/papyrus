import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import Card from '../common/Card';
import Button from '../common/Button';
import { useToast } from '../../hooks/useToast';
import { createWebhook, updateWebhook, deleteWebhook, type Webhook, type WebhookCreate } from '../../api/webhooks';
import { useWebhooks, useWebhookEvents, queryKeys } from '../../api/queries';

export default function WebhooksCard() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data: hooks = [] } = useWebhooks();
  const { data: events = [] } = useWebhookEvents();
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ name: '', url: '', secret: '', events: [] as string[] });

  const invalidateWebhooks = () => queryClient.invalidateQueries({ queryKey: queryKeys.webhooks });

  const createMutation = useMutation({
    mutationFn: (body: WebhookCreate) => createWebhook(body),
    meta: { suppressGlobalError: true },
    onSuccess: invalidateWebhooks,
    onError: () => toast.show('Failed to create webhook', 'error'),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, body }: { id: number; body: WebhookCreate }) => updateWebhook(id, body),
    meta: { suppressGlobalError: true },
    onSuccess: invalidateWebhooks,
    onError: () => toast.show('Failed to update webhook', 'error'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteWebhook(id),
    meta: { suppressGlobalError: true },
    onSuccess: invalidateWebhooks,
    onError: () => toast.show('Failed to delete webhook', 'error'),
  });

  const handleCreate = () => {
    if (!form.name || !form.url || form.events.length === 0) return;
    createMutation.mutate(form, {
      onSuccess: () => {
        setForm({ name: '', url: '', secret: '', events: [] });
        setShowAdd(false);
      },
    });
  };

  const toggleEnabled = (hook: Webhook) => {
    updateMutation.mutate({
      id: hook.id,
      body: { name: hook.name, url: hook.url, events: hook.events, enabled: !hook.enabled },
    });
  };

  const handleDelete = (id: number) => {
    deleteMutation.mutate(id);
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
