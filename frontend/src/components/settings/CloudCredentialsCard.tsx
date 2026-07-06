import Card from '../common/Card';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function CloudCredentialsCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card
      title="Cloud OAuth Credentials"
      description="OAuth app credentials that enable Google Drive, Dropbox, and OneDrive integration."
      collapsible
    >
      <div className="space-y-4">
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Google Drive</h4>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <SettingField label="Client ID" value={appSettings['gdrive_client_id'] ?? ''} onChange={set('gdrive_client_id')} mono />
            <SettingField label="Client secret" value={appSettings['gdrive_client_secret'] ?? ''} onChange={set('gdrive_client_secret')} type="password" />
          </div>
        </div>
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Dropbox</h4>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <SettingField label="App key" value={appSettings['dropbox_app_key'] ?? ''} onChange={set('dropbox_app_key')} mono />
            <SettingField label="App secret" value={appSettings['dropbox_app_secret'] ?? ''} onChange={set('dropbox_app_secret')} type="password" />
          </div>
        </div>
        <div className="space-y-3">
          <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">OneDrive</h4>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <SettingField label="Client ID" value={appSettings['onedrive_client_id'] ?? ''} onChange={set('onedrive_client_id')} mono />
            <SettingField label="Client secret" value={appSettings['onedrive_client_secret'] ?? ''} onChange={set('onedrive_client_secret')} type="password" />
          </div>
        </div>
        <div className="flex justify-end">
          <SaveButton section="cloud-creds" keys={['gdrive_client_id', 'gdrive_client_secret', 'dropbox_app_key', 'dropbox_app_secret', 'onedrive_client_id', 'onedrive_client_secret']} save={save} />
        </div>
      </div>
    </Card>
  );
}
