import Card from '../common/Card';
import Toggle from '../common/Toggle';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function AlertsCard({ appSettings, set, save }: SettingsSectionProps) {
  const alertsEnabled = appSettings['alerts_enabled'] === true || appSettings['alerts_enabled'] === 'true';

  return (
    <Card
      title="Supply & Error Alerts"
      description="Send an email and webhook when toner drops below the threshold or the printer reports a jam, open cover, or goes offline."
      collapsible
    >
      <div className="space-y-3">
        <Toggle
          checked={alertsEnabled}
          onChange={(v) => set('alerts_enabled')(String(v))}
          label="Enable supply & error alerts"
        />
        {alertsEnabled && (
          <div className="space-y-2 pl-6 border-l-2 border-ink-200 dark:border-ink-800">
            <SettingField
              label="Toner alert threshold (%)"
              value={appSettings['alert_toner_threshold'] ?? 20}
              onChange={set('alert_toner_threshold')}
              type="number"
            />
            <SettingField
              label="Alert email"
              value={appSettings['alert_email'] ?? ''}
              onChange={set('alert_email')}
              placeholder="admin@example.com"
            />
            <SettingField
              label="Poll interval (minutes)"
              value={appSettings['alert_poll_minutes'] ?? 5}
              onChange={set('alert_poll_minutes')}
              type="number"
            />
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Papyrus checks printer status on this interval and only re-notifies once a condition clears and recurs.
            </p>
          </div>
        )}
        <div className="flex justify-end">
          <SaveButton
            section="alerts"
            keys={['alerts_enabled', 'alert_toner_threshold', 'alert_email', 'alert_poll_minutes']}
            save={save}
          />
        </div>
      </div>
    </Card>
  );
}
