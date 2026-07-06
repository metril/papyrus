import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { useSettingsQuery, queryKeys } from '../api/queries';
import { updateSettings } from '../api/settings';
import Button from '../components/common/Button';
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

interface ApiErrorResponse {
  detail?: string;
}

/** Mirrors the pre-Query GET failure copy, staying silent on 401 (interceptor redirects). */
function describeSettingsLoadError(error: unknown): string | null {
  if (axios.isAxiosError<ApiErrorResponse>(error)) {
    if (error.response?.status === 401) return null;
    return `Failed to load settings: ${error.response?.data?.detail || error.message}`;
  }
  return `Failed to load settings: ${error instanceof Error ? error.message : String(error)}`;
}

export default function SettingsPage() {
  const { data, isLoading, isError, error } = useSettingsQuery();
  const [appSettings, setAppSettings] = useState<AppSettings | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, 'saving' | 'saved' | 'error'>>({});
  const queryClient = useQueryClient();

  // Seed the local draft from the query once data first arrives. Adjusting
  // state during render (rather than in an effect) avoids an extra
  // render-then-effect round trip; the null guard makes it fire exactly once
  // (StrictMode-safe) and skips re-seeding on a later background refetch —
  // in-progress edits and the post-save re-seed (below) own the draft after that.
  if (data && appSettings === null) {
    setAppSettings(data);
  }

  const settingsError = isError ? describeSettingsLoadError(error) : null;

  const saveMutation = useMutation({
    mutationFn: (payload: Record<string, string>) => updateSettings(payload),
    // The page renders its own per-section 'error' badge on failure, so don't
    // also fire the global error toast.
    meta: { suppressGlobalError: true },
  });

  const set = (key: string) => (value: string) => {
    setAppSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const saveSection = async (section: string, keys: string[]) => {
    setSaveStatus((s) => ({ ...s, [section]: 'saving' }));
    try {
      const payload: Record<string, string> = Object.fromEntries(
        keys.map((k) => [k, String(appSettings?.[k] ?? '')])
      );
      await saveMutation.mutateAsync(payload);
      // Refresh settings from backend to reflect actual stored values.
      await queryClient.invalidateQueries({ queryKey: queryKeys.settings });
      const fresh = queryClient.getQueryData<Record<string, string>>(queryKeys.settings);
      if (fresh) setAppSettings(fresh);
      setSaveStatus((s) => ({ ...s, [section]: 'saved' }));
      setTimeout(() => setSaveStatus((s) => ({ ...s, [section]: undefined as unknown as 'saved' })), 2000);
    } catch {
      setSaveStatus((s) => ({ ...s, [section]: 'error' }));
    }
  };

  const save: SaveControls = { status: saveStatus, onSave: saveSection, disabled: !!settingsError };
  const settingsForCards = appSettings ?? {};

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Settings</h2>

      {isLoading && (
        <div className="bg-ink-50 dark:bg-ink-950/40 border border-ink-200 dark:border-ink-800 text-ink-700 dark:text-ink-300 px-4 py-3 rounded-lg text-sm">
          Loading settings...
        </div>
      )}
      {settingsError && (
        <div className="bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900/50 text-red-700 dark:text-red-300 px-4 py-3 rounded-lg text-sm flex items-center justify-between gap-4">
          <span>{settingsError}</span>
          <Button size="sm" variant="secondary" className="shrink-0" onClick={() => window.location.reload()}>
            Retry
          </Button>
        </div>
      )}

      <PrintersCard />
      <ScannersCard />
      <NetworkServicesCard appSettings={settingsForCards} set={set} save={save} />
      <StorageCard appSettings={settingsForCards} set={set} save={save} />
      <ApplicationCard appSettings={settingsForCards} set={set} save={save} />
      <AuthenticationCard appSettings={settingsForCards} set={set} save={save} />
      <EmailSmtpCard appSettings={settingsForCards} set={set} save={save} />
      <EmailWebhookCard appSettings={settingsForCards} set={set} save={save} />
      <CloudCredentialsCard appSettings={settingsForCards} set={set} save={save} />
      <CloudStorageCard />
      <OcrCard appSettings={settingsForCards} set={set} save={save} />
      <ScanTemplateCard appSettings={settingsForCards} set={set} save={save} />
      <PaperlessCard appSettings={settingsForCards} set={set} save={save} />
      <FtpCard appSettings={settingsForCards} set={set} save={save} />
      <WebhooksCard />
      <BackupRestoreCard />
      <ApiTokensCard />
    </div>
  );
}
