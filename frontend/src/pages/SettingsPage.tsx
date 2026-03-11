import { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import api from '../api/client';
import { listProviders, disconnectProvider, getAuthorizeUrl } from '../api/cloud';
import type { APIToken, CloudProvider } from '../types';

const providerLabels: Record<string, string> = {
  gdrive: 'Google Drive',
  dropbox: 'Dropbox',
};

type AppSettings = Record<string, string | number | boolean>;

function SettingField({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
}: {
  label: string;
  value: string | number | boolean;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
}) {
  if (type === 'checkbox') {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(String(e.target.checked))}
          className="rounded border-gray-300"
        />
        <span className="font-medium text-gray-700">{label}</span>
      </label>
    );
  }
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <input
        type={type}
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-gray-300 text-sm p-2"
      />
    </div>
  );
}

export default function SettingsPage() {
  const [appSettings, setAppSettings] = useState<AppSettings>({});
  const [printerStatus, setPrinterStatus] = useState<Record<string, unknown> | null>(null);
  const [tokens, setTokens] = useState<APIToken[]>([]);
  const [newTokenName, setNewTokenName] = useState('');
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [cloudProviders, setCloudProviders] = useState<CloudProvider[]>([]);
  const [webhookInfo, setWebhookInfo] = useState<{ webhook_url: string; configured: boolean } | null>(null);
  const [webhookSecret, setWebhookSecret] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, 'saving' | 'saved' | 'error'>>({});

  useEffect(() => {
    api.get('/settings').then(({ data }) => setAppSettings(data)).catch(() => {});
    api.get('/printer/status').then(({ data }) => setPrinterStatus(data)).catch(() => {});
    api.get('/auth/tokens').then(({ data }) => setTokens(data)).catch(() => {});
    listProviders().then(setCloudProviders).catch(() => {});
    api.get('/email/webhook-info').then(({ data }) => setWebhookInfo(data)).catch(() => {});
  }, []);

  const set = (key: string) => (value: string) => {
    setAppSettings((prev) => ({ ...prev, [key]: value }));
  };

  const saveSection = async (section: string, keys: string[]) => {
    setSaveStatus((s) => ({ ...s, [section]: 'saving' }));
    try {
      const payload = Object.fromEntries(keys.map((k) => [k, appSettings[k]]));
      await api.put('/settings', payload);
      setSaveStatus((s) => ({ ...s, [section]: 'saved' }));
      setTimeout(() => setSaveStatus((s) => ({ ...s, [section]: undefined as unknown as 'saved' })), 2000);
    } catch {
      setSaveStatus((s) => ({ ...s, [section]: 'error' }));
    }
  };

  const SaveButton = ({ section, keys }: { section: string; keys: string[] }) => {
    const status = saveStatus[section];
    return (
      <Button
        onClick={() => saveSection(section, keys)}
        disabled={status === 'saving'}
        variant={status === 'error' ? 'danger' : 'primary'}
      >
        {status === 'saving' ? 'Saving…' : status === 'saved' ? 'Saved ✓' : status === 'error' ? 'Error' : 'Save'}
      </Button>
    );
  };

  const createToken = async () => {
    if (!newTokenName) return;
    try {
      const { data } = await api.post('/auth/tokens', {
        name: newTokenName,
        permissions: ['print', 'scan'],
      });
      setCreatedToken(data.token);
      setNewTokenName('');
      const { data: refreshed } = await api.get('/auth/tokens');
      setTokens(refreshed);
    } catch {
      alert('Failed to create token');
    }
  };

  const revokeToken = async (id: string) => {
    try {
      await api.delete(`/auth/tokens/${id}`);
      setTokens(tokens.filter((t) => t.id !== id));
    } catch {
      alert('Failed to revoke token');
    }
  };

  const handleDisconnectCloud = async (id: number) => {
    try {
      await disconnectProvider(id);
      setCloudProviders(cloudProviders.filter((p) => p.id !== id));
    } catch {
      alert('Failed to disconnect provider');
    }
  };

  const generateWebhookSecret = async () => {
    try {
      const { data } = await api.post('/email/webhook-secret');
      setWebhookSecret(data.secret);
      setWebhookInfo({ webhook_url: data.webhook_url, configured: true });
    } catch {
      alert('Failed to generate webhook secret');
    }
  };

  const testSmtp = async () => {
    try {
      await api.post('/email/test');
      alert('SMTP connection successful');
    } catch {
      alert('SMTP connection failed');
    }
  };

  const printerStateLabels: Record<number, string> = { 3: 'Idle', 4: 'Printing', 5: 'Stopped' };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Settings</h2>

      {/* Printer Status */}
      <Card title="Printer Status">
        {printerStatus ? (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-600">Status</span>
              <span className="font-medium">{printerStateLabels[printerStatus.state as number] || 'Unknown'}</span>
            </div>
            {typeof printerStatus.state_message === 'string' && printerStatus.state_message && (
              <div className="flex justify-between">
                <span className="text-gray-600">Message</span>
                <span>{printerStatus.state_message}</span>
              </div>
            )}
            <div className="flex justify-between">
              <span className="text-gray-600">Accepting Jobs</span>
              <span>{printerStatus.accepting_jobs ? 'Yes' : 'No'}</span>
            </div>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">Unable to connect to printer.</p>
        )}
      </Card>

      {/* Hardware */}
      <Card title="Hardware">
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <SettingField label="Printer Name (CUPS)" value={appSettings['printer_name'] ?? ''} onChange={set('printer_name')} placeholder="Brother_DCP_L2540DW" />
            <SettingField label="Printer URI" value={appSettings['printer_uri'] ?? ''} onChange={set('printer_uri')} placeholder="ipp://192.168.1.100/ipp" />
          </div>
          <SettingField label="Scanner Device (SANE)" value={appSettings['scanner_device'] ?? ''} onChange={set('scanner_device')} placeholder="airscan:w:Brother DCP-L2540DW" />
          <div className="flex justify-end">
            <SaveButton section="hardware" keys={['printer_name', 'printer_uri', 'scanner_device']} />
          </div>
        </div>
      </Card>

      {/* Network Services */}
      <Card title="Network Services">
        <div className="space-y-3">
          <p className="text-sm text-gray-600">mDNS/Bonjour advertisement for AirPrint and eSCL (AirScan).</p>
          <div className="space-y-2">
            <SettingField label="Enable AirPrint (network printer)" value={appSettings['network_printer_enabled'] ?? true} onChange={set('network_printer_enabled')} type="checkbox" />
            <SettingField label="Enable eSCL Scanner (AirScan)" value={appSettings['escl_enabled'] ?? true} onChange={set('escl_enabled')} type="checkbox" />
          </div>
          <SettingField label="Network Printer Name" value={appSettings['network_printer_name'] ?? ''} onChange={set('network_printer_name')} placeholder="Papyrus" />
          <div className="flex justify-end">
            <SaveButton section="network" keys={['network_printer_enabled', 'network_printer_name', 'escl_enabled']} />
          </div>
        </div>
      </Card>

      {/* Storage */}
      <Card title="Storage">
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <SettingField label="Scan Output Directory" value={appSettings['scan_dir'] ?? ''} onChange={set('scan_dir')} placeholder="/app/data/scans" />
            <SettingField label="Upload Directory" value={appSettings['upload_dir'] ?? ''} onChange={set('upload_dir')} placeholder="/app/data/uploads" />
            <SettingField label="Max Upload Size (MB)" value={appSettings['max_upload_size_mb'] ?? 50} onChange={set('max_upload_size_mb')} type="number" />
            <SettingField label="Scan Retention (days)" value={appSettings['scan_retention_days'] ?? 7} onChange={set('scan_retention_days')} type="number" />
          </div>
          <div className="flex justify-end">
            <SaveButton section="storage" keys={['scan_dir', 'upload_dir', 'max_upload_size_mb', 'scan_retention_days']} />
          </div>
        </div>
      </Card>

      {/* Application */}
      <Card title="Application">
        <div className="space-y-3">
          <SettingField label="Base URL" value={appSettings['base_url'] ?? ''} onChange={set('base_url')} placeholder="https://papyrus.example.com" />
          <SettingField label="Development Mode" value={appSettings['dev_mode'] ?? false} onChange={set('dev_mode')} type="checkbox" />
          <div className="flex justify-end">
            <SaveButton section="application" keys={['base_url', 'dev_mode']} />
          </div>
        </div>
      </Card>

      {/* Email / SMTP */}
      <Card title="Email (SMTP)">
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
            <SaveButton section="smtp" keys={['smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_from']} />
          </div>
        </div>
      </Card>

      {/* Email Webhook */}
      <Card title="Email Webhook">
        <div className="space-y-3">
          <p className="text-sm text-gray-600">
            External services can forward email attachments to Papyrus for automatic printing.
          </p>
          <SettingField label="Rate Limit (requests/min/IP)" value={appSettings['email_webhook_rate_limit'] ?? 10} onChange={set('email_webhook_rate_limit')} type="number" />
          <div className="flex justify-end">
            <SaveButton section="webhook-rate" keys={['email_webhook_rate_limit']} />
          </div>
          {webhookInfo && (
            <div className="space-y-2 pt-2 border-t border-gray-100">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Webhook URL</label>
                <code className="block text-xs bg-gray-100 p-2 rounded break-all">{webhookInfo.webhook_url}</code>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-600">Secret configured:</span>
                <span className={`text-sm font-medium ${webhookInfo.configured ? 'text-green-600' : 'text-red-600'}`}>
                  {webhookInfo.configured ? 'Yes' : 'No'}
                </span>
              </div>
            </div>
          )}
          {webhookSecret && (
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3">
              <p className="text-sm text-yellow-800 font-medium">
                Webhook secret generated! Copy it now &mdash; it won&apos;t be shown again:
              </p>
              <code className="text-xs break-all block mt-1 bg-yellow-100 p-2 rounded">{webhookSecret}</code>
            </div>
          )}
          <div className="flex justify-end">
            <Button size="sm" onClick={generateWebhookSecret}>
              {webhookInfo?.configured ? 'Regenerate Secret' : 'Generate Secret'}
            </Button>
          </div>
          <div className="text-xs text-gray-500 space-y-1">
            <p>Usage example:</p>
            <code className="block bg-gray-100 p-2 rounded break-all">
              curl -F &quot;token=YOUR_SECRET&quot; -F &quot;file=@document.pdf&quot; {webhookInfo?.webhook_url || 'https://papyrus.example.com/api/email/receive'}
            </code>
          </div>
        </div>
      </Card>

      {/* Cloud OAuth Credentials */}
      <Card title="Cloud OAuth Credentials">
        <div className="space-y-4">
          <p className="text-sm text-gray-600">
            Configure OAuth app credentials to enable Google Drive and Dropbox integration.
          </p>
          <div className="space-y-3">
            <h4 className="text-sm font-semibold text-gray-800">Google Drive</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <SettingField label="Client ID" value={appSettings['gdrive_client_id'] ?? ''} onChange={set('gdrive_client_id')} />
              <SettingField label="Client Secret" value={appSettings['gdrive_client_secret'] ?? ''} onChange={set('gdrive_client_secret')} type="password" />
            </div>
          </div>
          <div className="space-y-3">
            <h4 className="text-sm font-semibold text-gray-800">Dropbox</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <SettingField label="App Key" value={appSettings['dropbox_app_key'] ?? ''} onChange={set('dropbox_app_key')} />
              <SettingField label="App Secret" value={appSettings['dropbox_app_secret'] ?? ''} onChange={set('dropbox_app_secret')} type="password" />
            </div>
          </div>
          <div className="flex justify-end">
            <SaveButton section="cloud-creds" keys={['gdrive_client_id', 'gdrive_client_secret', 'dropbox_app_key', 'dropbox_app_secret']} />
          </div>
        </div>
      </Card>

      {/* Cloud Storage — connect/disconnect */}
      <Card title="Cloud Storage">
        <div className="space-y-4">
          {cloudProviders.length > 0 && (
            <div className="space-y-2">
              {cloudProviders.map((p) => (
                <div key={p.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200">
                  <div>
                    <div className="text-sm font-medium text-gray-900">{providerLabels[p.provider] || p.provider}</div>
                    <div className="text-xs text-gray-500">Connected {new Date(p.connected_at).toLocaleDateString()}</div>
                  </div>
                  <Button size="sm" variant="danger" onClick={() => handleDisconnectCloud(p.id)}>Disconnect</Button>
                </div>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <a href={getAuthorizeUrl('gdrive')}><Button size="sm" variant="secondary">Connect Google Drive</Button></a>
            <a href={getAuthorizeUrl('dropbox')}><Button size="sm" variant="secondary">Connect Dropbox</Button></a>
          </div>
        </div>
      </Card>

      {/* API Tokens */}
      <Card title="API Tokens">
        <div className="space-y-4">
          <div className="flex gap-2">
            <input
              type="text"
              placeholder="Token name"
              value={newTokenName}
              onChange={(e) => setNewTokenName(e.target.value)}
              className="flex-1 rounded-lg border border-gray-300 text-sm p-2"
            />
            <Button size="sm" onClick={createToken}>Create</Button>
          </div>
          {createdToken && (
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3">
              <p className="text-sm text-yellow-800 font-medium">
                Token created! Copy it now &mdash; it won&apos;t be shown again:
              </p>
              <code className="text-xs break-all block mt-1 bg-yellow-100 p-2 rounded">{createdToken}</code>
            </div>
          )}
          {tokens.length === 0 ? (
            <p className="text-gray-500 text-sm">No API tokens created.</p>
          ) : (
            <div className="space-y-2">
              {tokens.map((token) => (
                <div key={token.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200">
                  <div>
                    <div className="text-sm font-medium text-gray-900">{token.name}</div>
                    <div className="text-xs text-gray-500">
                      {token.permissions.join(', ')}
                      {token.last_used_at && ` · Last used: ${new Date(token.last_used_at).toLocaleDateString()}`}
                    </div>
                  </div>
                  <Button size="sm" variant="danger" onClick={() => revokeToken(token.id)}>Revoke</Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
