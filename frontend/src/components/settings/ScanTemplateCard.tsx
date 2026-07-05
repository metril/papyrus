import Card from '../common/Card';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function ScanTemplateCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card title="Scan Filename Template" collapsible>
      <div className="space-y-3">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Template for naming delivered scan files. Variables: {'{date}'}, {'{time}'}, {'{datetime}'}, {'{id}'}, {'{resolution}'}, {'{mode}'}, {'{format}'}, {'{pages}'}, {'{counter}'}.
        </p>
        <SettingField
          label="Template"
          value={appSettings.scan_filename_template ?? 'scan_{date}_{time}_{id}'}
          onChange={set('scan_filename_template')}
          placeholder="scan_{date}_{time}_{id}"
        />
        <div className="flex justify-end">
          <SaveButton section="scan_template" keys={['scan_filename_template']} save={save} />
        </div>
      </div>
    </Card>
  );
}
