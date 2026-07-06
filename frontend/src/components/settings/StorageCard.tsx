import Card from '../common/Card';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function StorageCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card
      title="Storage"
      description="Where files land on disk, and how long they're kept."
      collapsible
      defaultOpen
    >
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <SettingField label="Scan output directory" value={appSettings['scan_dir'] ?? ''} onChange={set('scan_dir')} placeholder="/app/data/scans" mono />
          <SettingField label="Upload directory" value={appSettings['upload_dir'] ?? ''} onChange={set('upload_dir')} placeholder="/app/data/uploads" mono />
          <SettingField label="Max upload size (MB)" value={appSettings['max_upload_size_mb'] ?? 50} onChange={set('max_upload_size_mb')} type="number" />
          <SettingField label="Scan retention (days)" value={appSettings['scan_retention_days'] ?? 7} onChange={set('scan_retention_days')} type="number" />
          <SettingField label="Print retention (days)" value={appSettings['print_retention_days'] ?? 30} onChange={set('print_retention_days')} type="number" />
        </div>
        <div className="flex justify-end">
          <SaveButton section="storage" keys={['scan_dir', 'upload_dir', 'max_upload_size_mb', 'scan_retention_days', 'print_retention_days']} save={save} />
        </div>
      </div>
    </Card>
  );
}
