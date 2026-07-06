import Card from '../common/Card';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function NetworkServicesCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card
      title="Network Services"
      description="eSCL (AirScan) enables network scanner discovery by macOS and iOS."
      collapsible
      defaultOpen
    >
      <div className="space-y-3">
        <SettingField label="Enable eSCL scanner (AirScan)" value={appSettings['escl_enabled'] ?? true} onChange={set('escl_enabled')} type="checkbox" />
        <div className="flex justify-end">
          <SaveButton section="network" keys={['escl_enabled']} save={save} />
        </div>
      </div>
    </Card>
  );
}
