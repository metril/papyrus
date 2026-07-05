import Card from '../common/Card';
import Button from '../common/Button';
import { useToast } from '../../hooks/useToast';
import api from '../../api/client';

export default function BackupRestoreCard() {
  const toast = useToast();

  return (
    <Card title="Backup / Restore" collapsible>
      <div className="space-y-3">
        <p className="text-sm text-gray-600 dark:text-gray-400">Export all application settings as JSON, or restore from a previous backup.</p>
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onClick={async () => {
            try {
              const { data } = await api.get('/admin/backup');
              const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `papyrus-backup-${new Date().toISOString().slice(0, 10)}.json`;
              a.click();
              URL.revokeObjectURL(url);
              toast.show('Backup downloaded', 'success');
            } catch { toast.show('Failed to export backup'); }
          }}>
            Export Backup
          </Button>
          <label className="cursor-pointer">
            <Button size="sm" variant="secondary" onClick={() => document.getElementById('restore-file')?.click()}>
              Restore Backup
            </Button>
            <input id="restore-file" type="file" accept=".json" className="hidden" onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              if (!window.confirm('This will overwrite all current settings. Continue?')) {
                e.target.value = '';
                return;
              }
              try {
                const text = await file.text();
                let data;
                try { data = JSON.parse(text); } catch { toast.show('Invalid JSON file'); e.target.value = ''; return; }
                await api.post('/admin/restore', data);
                toast.show('Settings restored — reloading...', 'success');
                setTimeout(() => window.location.reload(), 1000);
              } catch { toast.show('Failed to restore backup'); }
              e.target.value = '';
            }} />
          </label>
        </div>
      </div>
    </Card>
  );
}
