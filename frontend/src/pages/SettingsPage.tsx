import { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import api from '../api/client';
import type { APIToken } from '../types';

export default function SettingsPage() {
  const [printerStatus, setPrinterStatus] = useState<Record<string, unknown> | null>(null);
  const [tokens, setTokens] = useState<APIToken[]>([]);
  const [newTokenName, setNewTokenName] = useState('');
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [smtpHost, setSmtpHost] = useState('');
  const [smtpPort, setSmtpPort] = useState(587);
  const [smtpUser, setSmtpUser] = useState('');
  const [smtpPassword, setSmtpPassword] = useState('');
  const [smtpFrom, setSmtpFrom] = useState('');

  useEffect(() => {
    api.get('/printer/status').then(({ data }) => setPrinterStatus(data)).catch(() => {});
    api.get('/auth/tokens').then(({ data }) => setTokens(data)).catch(() => {});
    api.get('/email/config').then(({ data }) => {
      if (data.smtp_host) setSmtpHost(data.smtp_host);
      if (data.smtp_from) setSmtpFrom(data.smtp_from);
    }).catch(() => {});
  }, []);

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

  const saveSmtp = async () => {
    try {
      await api.put('/email/config', {
        smtp_host: smtpHost,
        smtp_port: smtpPort,
        smtp_user: smtpUser,
        smtp_password: smtpPassword,
        smtp_from: smtpFrom,
      });
      alert('SMTP configuration saved');
    } catch {
      alert('Failed to save SMTP configuration');
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

  const printerStateLabels: Record<number, string> = {
    3: 'Idle',
    4: 'Printing',
    5: 'Stopped',
  };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900">Settings</h2>

      {/* Printer Status */}
      <Card title="Printer">
        {printerStatus ? (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-600">Status</span>
              <span className="font-medium">
                {printerStateLabels[printerStatus.state as number] || 'Unknown'}
              </span>
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
                Token created! Copy it now &mdash; it won't be shown again:
              </p>
              <code className="text-xs break-all block mt-1 bg-yellow-100 p-2 rounded">
                {createdToken}
              </code>
            </div>
          )}

          {tokens.length === 0 ? (
            <p className="text-gray-500 text-sm">No API tokens created.</p>
          ) : (
            <div className="space-y-2">
              {tokens.map((token) => (
                <div
                  key={token.id}
                  className="flex items-center justify-between p-3 rounded-lg border border-gray-200"
                >
                  <div>
                    <div className="text-sm font-medium text-gray-900">{token.name}</div>
                    <div className="text-xs text-gray-500">
                      {token.permissions.join(', ')}
                      {token.last_used_at && ` \u00b7 Last used: ${new Date(token.last_used_at).toLocaleDateString()}`}
                    </div>
                  </div>
                  <Button size="sm" variant="danger" onClick={() => revokeToken(token.id)}>
                    Revoke
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {/* Email / SMTP */}
      <Card title="Email (SMTP)">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">SMTP Host</label>
              <input
                type="text"
                value={smtpHost}
                onChange={(e) => setSmtpHost(e.target.value)}
                className="w-full rounded-lg border border-gray-300 text-sm p-2"
                placeholder="smtp.example.com"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Port</label>
              <input
                type="number"
                value={smtpPort}
                onChange={(e) => setSmtpPort(Number(e.target.value))}
                className="w-full rounded-lg border border-gray-300 text-sm p-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Username</label>
              <input
                type="text"
                value={smtpUser}
                onChange={(e) => setSmtpUser(e.target.value)}
                className="w-full rounded-lg border border-gray-300 text-sm p-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
              <input
                type="password"
                value={smtpPassword}
                onChange={(e) => setSmtpPassword(e.target.value)}
                className="w-full rounded-lg border border-gray-300 text-sm p-2"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">From Address</label>
            <input
              type="email"
              value={smtpFrom}
              onChange={(e) => setSmtpFrom(e.target.value)}
              className="w-full rounded-lg border border-gray-300 text-sm p-2"
              placeholder="papyrus@example.com"
            />
          </div>
          <div className="flex gap-2">
            <Button onClick={saveSmtp}>Save</Button>
            <Button variant="secondary" onClick={testSmtp}>Test Connection</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
