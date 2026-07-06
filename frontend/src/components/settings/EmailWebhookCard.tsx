import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import Card from '../common/Card';
import Button from '../common/Button';
import { useToast } from '../../hooks/useToast';
import { useEmailWebhookQuery, queryKeys } from '../../api/queries';
import { generateEmailWebhookSecret } from '../../api/settings';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function EmailWebhookCard({ appSettings, set, save }: SettingsSectionProps) {
  const toast = useToast();
  const [webhookSecret, setWebhookSecret] = useState<string | null>(null);
  const { data: webhookInfo } = useEmailWebhookQuery();
  const queryClient = useQueryClient();

  const generateSecretMutation = useMutation({
    mutationFn: generateEmailWebhookSecret,
    meta: { suppressGlobalError: true },
    onSuccess: (data) => {
      setWebhookSecret(data.secret);
      queryClient.setQueryData(queryKeys.emailWebhook, { webhook_url: data.webhook_url, configured: true });
    },
    onError: () => toast.show('Failed to generate webhook secret'),
  });

  return (
    <Card title="Email Webhook" collapsible>
      <div className="space-y-3">
        <p className="text-sm text-gray-600 dark:text-gray-400">
          External services can forward email attachments to Papyrus for automatic printing.
        </p>
        <SettingField label="Rate Limit (requests/min/IP)" value={appSettings['email_webhook_rate_limit'] ?? 10} onChange={set('email_webhook_rate_limit')} type="number" />
        <div className="flex justify-end">
          <SaveButton section="webhook-rate" keys={['email_webhook_rate_limit']} save={save} />
        </div>
        {webhookInfo && (
          <div className="space-y-2 pt-2 border-t border-gray-100 dark:border-gray-800">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Webhook URL</label>
              <code className="block text-xs bg-gray-100 dark:bg-gray-800 dark:text-gray-300 p-2 rounded break-all">{webhookInfo.webhook_url}</code>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-600 dark:text-gray-400">Secret configured:</span>
              <span className={`text-sm font-medium ${webhookInfo.configured ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
                {webhookInfo.configured ? 'Yes' : 'No'}
              </span>
            </div>
          </div>
        )}
        {webhookSecret && (
          <div className="bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-800 rounded-lg p-3">
            <p className="text-sm text-yellow-800 dark:text-yellow-300 font-medium">
              Webhook secret generated! Copy it now &mdash; it won&apos;t be shown again:
            </p>
            <code className="text-xs break-all block mt-1 bg-yellow-100 dark:bg-yellow-900/50 dark:text-yellow-200 p-2 rounded">{webhookSecret}</code>
          </div>
        )}
        <div className="flex justify-end">
          <Button size="sm" onClick={() => generateSecretMutation.mutate()}>
            {webhookInfo?.configured ? 'Regenerate Secret' : 'Generate Secret'}
          </Button>
        </div>
        <div className="text-xs text-gray-500 dark:text-gray-400 space-y-1">
          <p>Usage example:</p>
          <code className="block bg-gray-100 dark:bg-gray-800 dark:text-gray-300 p-2 rounded break-all">
            curl -F &quot;token=YOUR_SECRET&quot; -F &quot;file=@document.pdf&quot; {webhookInfo?.webhook_url || 'https://papyrus.example.com/api/email/receive'}
          </code>
        </div>
      </div>
    </Card>
  );
}
