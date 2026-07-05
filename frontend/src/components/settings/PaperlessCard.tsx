import Card from '../common/Card';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function PaperlessCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card title="Paperless-ngx" collapsible>
      <div className="space-y-3">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Send scans directly to your Paperless-ngx instance for archiving and OCR.
        </p>
        <SettingField
          label="Paperless URL"
          value={appSettings.paperless_url ?? ''}
          onChange={set('paperless_url')}
          placeholder="https://paperless.example.com"
        />
        <SettingField
          label="API Token"
          value={appSettings.paperless_api_token ?? ''}
          onChange={set('paperless_api_token')}
          type="password"
          placeholder="Token from Paperless admin"
        />
        <div className="flex justify-end">
          <SaveButton section="paperless" keys={['paperless_url', 'paperless_api_token']} save={save} />
        </div>
      </div>
    </Card>
  );
}
