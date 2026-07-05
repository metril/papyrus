import { useState, useEffect } from 'react';
import api from '../api/client';
import type { AppSettings, SaveControls } from '../components/settings/shared';
import PrintersCard from '../components/settings/PrintersCard';
import ScannersCard from '../components/settings/ScannersCard';
import NetworkServicesCard from '../components/settings/NetworkServicesCard';
import StorageCard from '../components/settings/StorageCard';
import ApplicationCard from '../components/settings/ApplicationCard';
import AuthenticationCard from '../components/settings/AuthenticationCard';
import EmailSmtpCard from '../components/settings/EmailSmtpCard';
import EmailWebhookCard from '../components/settings/EmailWebhookCard';
import CloudCredentialsCard from '../components/settings/CloudCredentialsCard';
import CloudStorageCard from '../components/settings/CloudStorageCard';
import OcrCard from '../components/settings/OcrCard';
import ScanTemplateCard from '../components/settings/ScanTemplateCard';
import PaperlessCard from '../components/settings/PaperlessCard';
import FtpCard from '../components/settings/FtpCard';
import WebhooksCard from '../components/settings/WebhooksCard';
import BackupRestoreCard from '../components/settings/BackupRestoreCard';
import ApiTokensCard from '../components/settings/ApiTokensCard';

export default function SettingsPage() {
  const [appSettings, setAppSettings] = useState<AppSettings>({});
  const [saveStatus, setSaveStatus] = useState<Record<string, 'saving' | 'saved' | 'error'>>({});
  const [settingsLoading, setSettingsLoading] = useState(true);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  useEffect(() => {
    api.get('/settings')
      .then(({ data }) => {
        setAppSettings(data);
        setSettingsLoading(false);
      })
      .catch((err) => {
        if (err.response?.status !== 401) {
          setSettingsError(
            `Failed to load settings: ${err.response?.data?.detail || err.message}`
          );
        }
        setSettingsLoading(false);
      });
  }, []);

  const set = (key: string) => (value: string) => {
    setAppSettings((prev) => ({ ...prev, [key]: value }));
  };

  const saveSection = async (section: string, keys: string[]) => {
    setSaveStatus((s) => ({ ...s, [section]: 'saving' }));
    try {
      const payload = Object.fromEntries(keys.map((k) => [k, appSettings[k]]));
      await api.put('/settings', payload);
      // Refresh settings from backend to reflect actual stored values
      const { data } = await api.get('/settings');
      setAppSettings(data);
      setSaveStatus((s) => ({ ...s, [section]: 'saved' }));
      setTimeout(() => setSaveStatus((s) => ({ ...s, [section]: undefined as unknown as 'saved' })), 2000);
    } catch {
      setSaveStatus((s) => ({ ...s, [section]: 'error' }));
    }
  };

  const save: SaveControls = { status: saveStatus, onSave: saveSection, disabled: !!settingsError };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Settings</h2>

      {settingsLoading && (
        <div className="bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-300 px-4 py-3 rounded">
          Loading settings...
        </div>
      )}
      {settingsError && (
        <div className="bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 px-4 py-3 rounded flex items-center justify-between">
          <span>{settingsError}</span>
          <button
            className="ml-4 px-3 py-1 bg-red-100 dark:bg-red-800 hover:bg-red-200 dark:hover:bg-red-700 rounded text-sm"
            onClick={() => window.location.reload()}
          >
            Retry
          </button>
        </div>
      )}

      <PrintersCard />
      <ScannersCard />
      <NetworkServicesCard appSettings={appSettings} set={set} save={save} />
      <StorageCard appSettings={appSettings} set={set} save={save} />
      <ApplicationCard appSettings={appSettings} set={set} save={save} />
      <AuthenticationCard appSettings={appSettings} set={set} save={save} />
      <EmailSmtpCard appSettings={appSettings} set={set} save={save} />
      <EmailWebhookCard appSettings={appSettings} set={set} save={save} />
      <CloudCredentialsCard appSettings={appSettings} set={set} save={save} />
      <CloudStorageCard />
      <OcrCard appSettings={appSettings} set={set} save={save} />
      <ScanTemplateCard appSettings={appSettings} set={set} save={save} />
      <PaperlessCard appSettings={appSettings} set={set} save={save} />
      <FtpCard appSettings={appSettings} set={set} save={save} />
      <WebhooksCard />
      <BackupRestoreCard />
      <ApiTokensCard />
    </div>
  );
}
