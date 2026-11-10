import Card from '../common/Card';
import Toggle from '../common/Toggle';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

const isOn = (v: unknown, fallback: boolean) =>
  v === undefined || v === '' ? fallback : v === true || v === 'true';

export default function ApiCard({ appSettings, set, save }: SettingsSectionProps) {
  const limitEnabled = isOn(appSettings['rate_limit_enabled'], true);
  const publicDocs = isOn(appSettings['public_api_docs'], false);

  return (
    <Card
      title="API"
      description="Rate limits for API requests and access to the interactive API documentation."
      collapsible
    >
      <div className="space-y-3">
        <Toggle
          checked={limitEnabled}
          onChange={(v) => set('rate_limit_enabled')(String(v))}
          label="Enable API rate limiting"
        />
        {limitEnabled && (
          <div className="space-y-2 pl-6 border-l-2 border-ink-200 dark:border-ink-800">
            <SettingField
              label="Requests per minute per API token"
              value={appSettings['rate_limit_token_rpm'] ?? 600}
              onChange={set('rate_limit_token_rpm')}
              type="number"
            />
            <SettingField
              label="Requests per minute per IP address"
              value={appSettings['rate_limit_ip_rpm'] ?? 300}
              onChange={set('rate_limit_ip_rpm')}
              type="number"
            />
            <SettingField
              label="Trusted proxies"
              value={appSettings['trusted_proxies'] ?? '127.0.0.1/32,::1/128,172.16.0.0/12'}
              onChange={set('trusted_proxies')}
            />
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Comma-separated IPs or CIDR ranges of your reverse proxy. Only requests arriving from these
              addresses may identify the real client through X-Forwarded-For; every other client is limited
              by its own address.
            </p>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Requests over the limit get a 429 response with a Retry-After header. Health checks, scanner
              discovery and local print ingest are never limited.
            </p>
          </div>
        )}
        <Toggle
          checked={publicDocs}
          onChange={(v) => set('public_api_docs')(String(v))}
          label="Make API documentation public"
        />
        <p className="text-xs text-gray-500 dark:text-gray-400">
          When off, the documentation requires signing in.{' '}
          <a href="/docs" target="_blank" rel="noreferrer" className="text-ink-600 dark:text-ink-400 underline">
            Open API docs
          </a>
        </p>
        <div className="flex justify-end">
          <SaveButton
            section="api"
            keys={[
              'rate_limit_enabled',
              'rate_limit_token_rpm',
              'rate_limit_ip_rpm',
              'trusted_proxies',
              'public_api_docs',
            ]}
            save={save}
          />
        </div>
      </div>
    </Card>
  );
}
