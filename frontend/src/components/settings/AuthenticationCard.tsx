import Card from '../common/Card';
import Toggle from '../common/Toggle';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function AuthenticationCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card
      title="Authentication"
      description="Local login and OIDC single sign-on."
      collapsible
      defaultOpen
    >
      <div className="space-y-3">
        <Toggle
          checked={appSettings['local_auth_enabled'] === true || appSettings['local_auth_enabled'] === 'true' || appSettings['local_auth_enabled'] === undefined}
          onChange={(v) => set('local_auth_enabled')(v ? 'true' : 'false')}
          label="Enable local login"
        />
        <Toggle
          checked={appSettings['oidc_enabled'] === true || appSettings['oidc_enabled'] === 'true'}
          onChange={(v) => set('oidc_enabled')(v ? 'true' : 'false')}
          label="Enable OIDC / SSO"
        />
        {(appSettings['oidc_enabled'] === true || appSettings['oidc_enabled'] === 'true') && (
          <div className="space-y-2 pl-6 border-l-2 border-ink-200 dark:border-ink-800">
            <SettingField label="Issuer URL" value={appSettings['oidc_issuer'] ?? ''} onChange={set('oidc_issuer')} placeholder="https://auth.example.com/application/o/papyrus/" mono />
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <SettingField label="Client ID" value={appSettings['oidc_client_id'] ?? ''} onChange={set('oidc_client_id')} mono />
              <SettingField label="Client secret" value={appSettings['oidc_client_secret'] ?? ''} onChange={set('oidc_client_secret')} type="password" />
            </div>
            <SettingField label="Scopes" value={appSettings['oidc_scopes'] ?? 'openid email profile'} onChange={set('oidc_scopes')} placeholder="openid email profile groups" mono />
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <SettingField label="Admin group" value={appSettings['oidc_admin_group'] ?? ''} onChange={set('oidc_admin_group')} placeholder="papyrus-admins" mono />
              <SettingField label="Groups claim" value={appSettings['oidc_groups_claim'] ?? 'groups'} onChange={set('oidc_groups_claim')} placeholder="groups" mono />
            </div>
            <p className="text-xs text-gray-500 dark:text-gray-400">Users in the admin group get admin role. Changing OIDC settings requires an app restart to take effect.</p>
          </div>
        )}
        <div className="flex justify-end">
          <SaveButton section="auth" keys={['local_auth_enabled', 'oidc_enabled', 'oidc_issuer', 'oidc_client_id', 'oidc_client_secret', 'oidc_scopes', 'oidc_admin_group', 'oidc_groups_claim']} save={save} />
        </div>
      </div>
    </Card>
  );
}
