import Card from '../common/Card';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function FtpCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card
      title="FTP / SFTP"
      description="Upload scans to an FTP or SFTP server. Used as a post-scan delivery target."
      collapsible
    >
      <div className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <SettingField label="Host" value={appSettings.ftp_host ?? ''} onChange={set('ftp_host')} placeholder="ftp.example.com" mono />
          <SettingField label="Port" value={appSettings.ftp_port ?? '21'} onChange={set('ftp_port')} placeholder="21" mono />
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <SettingField label="Username" value={appSettings.ftp_username ?? ''} onChange={set('ftp_username')} />
          <SettingField label="Password" value={appSettings.ftp_password ?? ''} onChange={set('ftp_password')} type="password" />
        </div>
        <SettingField label="Remote directory" value={appSettings.ftp_remote_dir ?? '/'} onChange={set('ftp_remote_dir')} placeholder="/" mono />
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Protocol</label>
          <select
            value={String(appSettings.ftp_protocol ?? 'ftp')}
            onChange={(e) => set('ftp_protocol')(e.target.value)}
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="ftp">FTP</option>
            <option value="ftps">FTPS (FTP over TLS)</option>
            <option value="sftp">SFTP (SSH)</option>
          </select>
        </div>
        <div className="flex justify-end">
          <SaveButton section="ftp" keys={['ftp_host', 'ftp_port', 'ftp_username', 'ftp_password', 'ftp_remote_dir', 'ftp_protocol']} save={save} />
        </div>
      </div>
    </Card>
  );
}
