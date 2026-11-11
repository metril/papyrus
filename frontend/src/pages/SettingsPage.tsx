import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { useSettingsQuery, queryKeys } from '../api/queries';
import { updateSettings } from '../api/settings';
import Button from '../components/common/Button';
import type { AppSettings, SaveControls } from '../components/settings/shared';
import PrintersCard from '../components/settings/PrintersCard';
import ScannersCard from '../components/settings/ScannersCard';
import NetworkServicesCard from '../components/settings/NetworkServicesCard';
import AlertsCard from '../components/settings/AlertsCard';
import ApiCard from '../components/settings/ApiCard';
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
import KiosksCard from '../components/kiosks/KiosksCard';
import ContactsCard from '../components/kiosks/ContactsCard';
import { useAuthStore } from '../store/authStore';

const TABS = [['general', 'General'], ['kiosks', 'Kiosks'], ['contacts', 'Address book']] as const;
type Tab = (typeof TABS)[number][0];

interface ApiErrorResponse {
  detail?: string;
}

/** Shallow-picks only `keys` present on `source` — used to merge a section's
 * freshly-saved values back into the shared draft (F85) without letting the
 * untouched keys of other, still-unsaved sections be clobbered by whatever
 * happens to be in `source`. */
function pick(source: Record<string, string>, keys: string[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const key of keys) {
    if (key in source) result[key] = source[key];
  }
  return result;
}

/** Mirrors the pre-Query GET failure copy, staying silent on 401 (the
 * interceptor signs the user out — see api/client.ts — which unmounts this
 * page entirely in favor of LoginScreen). */
function describeSettingsLoadError(error: unknown): string | null {
  if (axios.isAxiosError<ApiErrorResponse>(error)) {
    if (error.response?.status === 401) return null;
    return `Failed to load settings: ${error.response?.data?.detail || error.message}`;
  }
  return `Failed to load settings: ${error instanceof Error ? error.message : String(error)}`;
}

export default function SettingsPage() {
  const [appSettings, setAppSettings] = useState<AppSettings | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, 'saving' | 'saved' | 'error'>>({});
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const { data, isLoading, isError, error } = useSettingsQuery(isAdmin);
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get('tab');
  // General and Kiosks are admin-only; everyone has Address book. Unknown or
  // disallowed ids fall back to the first visible tab.
  const visibleTabs = TABS.filter(([id]) => isAdmin || id === 'contacts');
  const tab: Tab = visibleTabs.find(([id]) => id === requested)?.[0] ?? visibleTabs[0][0];
  const setTab = (id: Tab) =>
    setSearchParams(id === 'general' ? {} : { tab: id }, { replace: true });

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
      // F85: merge back only this section's own keys — replacing the whole
      // draft with `fresh` silently discarded any unsaved edit sitting in a
      // different card (fresh reflects only what's actually in the DB).
      if (fresh) setAppSettings((prev) => (prev ? { ...prev, ...pick(fresh, keys) } : prev));
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
      <div className="flex flex-wrap items-center gap-4">
        <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Settings</h2>
        <div role="tablist" className="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
          {visibleTabs.map(([id, label]) => (
              <button
                key={id}
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`px-3 py-1.5 text-sm rounded-md font-medium transition-colors ${
                  tab === id
                    ? 'bg-ink-600 text-white shadow-sm dark:bg-ink-500'
                    : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100'
                }`}
              >
                {label}
              </button>
            ))}
        </div>
      </div>

      {tab === 'kiosks' && isAdmin && <KiosksCard />}
      {tab === 'contacts' && <ContactsCard />}
      {tab === 'general' && (<>

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
      <AlertsCard appSettings={settingsForCards} set={set} save={save} />
      <ApiCard appSettings={settingsForCards} set={set} save={save} />
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
      </>)}
    </div>
  );
}
