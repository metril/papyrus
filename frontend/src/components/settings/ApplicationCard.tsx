import Card from '../common/Card';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function ApplicationCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card title="Application" collapsible defaultOpen>
      <div className="space-y-3">
        <SettingField label="Base URL" value={appSettings['base_url'] ?? ''} onChange={set('base_url')} placeholder="https://papyrus.example.com" />
        <SettingField label="Development Mode" value={appSettings['dev_mode'] ?? false} onChange={set('dev_mode')} type="checkbox" />
        <SettingField label="Require Release PIN" value={appSettings['require_release_pin'] ?? false} onChange={set('require_release_pin')} type="checkbox" />
        <p className="text-xs text-gray-500 dark:text-gray-400">When enabled, uploaded jobs get a randomly generated PIN required at release time.</p>
        <div className="flex justify-end">
          <SaveButton section="application" keys={['base_url', 'dev_mode', 'require_release_pin']} save={save} />
        </div>
      </div>
    </Card>
  );
}
