import { useMutation } from '@tanstack/react-query';
import Card from '../common/Card';
import Button from '../common/Button';
import { useToast } from '../../hooks/useToast';
import { getBackup, restoreBackup } from '../../api/admin';

export default function BackupRestoreCard() {
  const toast = useToast();

  // Neither action has a cached list to invalidate — export downloads a
  // one-off file, and restore reloads the whole page a second later, which
  // re-fetches everything from scratch anyway.
  const exportMutation = useMutation({
    mutationFn: () => getBackup(),
    meta: { suppressGlobalError: true },
    onSuccess: (data) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `papyrus-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast.show('Backup downloaded', 'success');
    },
    onError: () => toast.show('Failed to export backup'),
  });

  const restoreMutation = useMutation({
    mutationFn: (data: unknown) => restoreBackup(data),
    meta: { suppressGlobalError: true },
    onSuccess: () => {
      toast.show('Settings restored — reloading...', 'success');
      setTimeout(() => window.location.reload(), 1000);
    },
    onError: () => toast.show('Failed to restore backup'),
  });

  return (
    <Card title="Backup / Restore" collapsible>
      <div className="space-y-3">
        <p className="text-sm text-gray-600 dark:text-gray-400">Export all application settings as JSON, or restore from a previous backup.</p>
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onClick={() => exportMutation.mutate()}>
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
              const text = await file.text();
              let data;
              try { data = JSON.parse(text); } catch { toast.show('Invalid JSON file'); e.target.value = ''; return; }
              try {
                await restoreMutation.mutateAsync(data);
              } catch { /* toast handled by restoreMutation.onError */ }
              e.target.value = '';
            }} />
          </label>
        </div>
      </div>
    </Card>
  );
}
