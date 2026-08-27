import { useMutation } from '@tanstack/react-query';
import Card from '../common/Card';
import Button from '../common/Button';
import { useToast } from '../../hooks/useToast';
import { sendTestEmail } from '../../api/settings';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function EmailSmtpCard({ appSettings, set, save }: SettingsSectionProps) {
  const toast = useToast();

  const testSmtpMutation = useMutation({
    mutationFn: sendTestEmail,
    meta: { suppressGlobalError: true },
    onSuccess: () => toast.show('SMTP connection successful', 'success'),
    onError: () => toast.show('SMTP connection failed'),
  });

  return (
    <Card
      title="Email (SMTP)"
      description="Outgoing mail server for sending scans by email."
      collapsible
    >
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <SettingField label="SMTP host" value={appSettings['smtp_host'] ?? ''} onChange={set('smtp_host')} placeholder="smtp.example.com" mono />
          <SettingField label="Port" value={appSettings['smtp_port'] ?? 587} onChange={set('smtp_port')} type="number" />
          <SettingField label="Username" value={appSettings['smtp_user'] ?? ''} onChange={set('smtp_user')} />
          <SettingField label="Password" value={appSettings['smtp_password'] ?? ''} onChange={set('smtp_password')} type="password" />
        </div>
        <SettingField label="From address" value={appSettings['smtp_from'] ?? ''} onChange={set('smtp_from')} placeholder="papyrus@example.com" mono />
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Security</label>
          <select
            value={String(appSettings['smtp_security'] ?? 'starttls')}
            onChange={(e) => set('smtp_security')(e.target.value)}
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="starttls">STARTTLS (recommended)</option>
            <option value="tls">TLS (implicit, e.g. port 465)</option>
            <option value="none">None (unencrypted)</option>
          </select>
        </div>
        <div className="flex gap-2 justify-end">
          <Button variant="secondary" onClick={() => testSmtpMutation.mutate()}>Test connection</Button>
          <SaveButton section="smtp" keys={['smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_from', 'smtp_security']} save={save} />
        </div>
      </div>
    </Card>
  );
}
