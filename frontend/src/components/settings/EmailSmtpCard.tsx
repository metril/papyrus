import Card from '../common/Card';
import Button from '../common/Button';
import { useToast } from '../../hooks/useToast';
import api from '../../api/client';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function EmailSmtpCard({ appSettings, set, save }: SettingsSectionProps) {
  const toast = useToast();

  const testSmtp = async () => {
    try {
      await api.post('/email/test');
      toast.show('SMTP connection successful', 'success');
    } catch {
      toast.show('SMTP connection failed');
    }
  };

  return (
    <Card title="Email (SMTP)" collapsible>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <SettingField label="SMTP Host" value={appSettings['smtp_host'] ?? ''} onChange={set('smtp_host')} placeholder="smtp.example.com" />
          <SettingField label="Port" value={appSettings['smtp_port'] ?? 587} onChange={set('smtp_port')} type="number" />
          <SettingField label="Username" value={appSettings['smtp_user'] ?? ''} onChange={set('smtp_user')} />
          <SettingField label="Password" value={appSettings['smtp_password'] ?? ''} onChange={set('smtp_password')} type="password" />
        </div>
        <SettingField label="From Address" value={appSettings['smtp_from'] ?? ''} onChange={set('smtp_from')} placeholder="papyrus@example.com" />
        <div className="flex gap-2 justify-end">
          <Button variant="secondary" onClick={testSmtp}>Test Connection</Button>
          <SaveButton section="smtp" keys={['smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_from']} save={save} />
        </div>
      </div>
    </Card>
  );
}
