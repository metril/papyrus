import { useState, useEffect } from 'react';
import Card from '../common/Card';
import Button from '../common/Button';
import Toggle from '../common/Toggle';
import { useToast } from '../../hooks/useToast';
import {
  listPrinters,
  addPrinter,
  updatePrinter,
  deletePrinter,
  setDefaultPrinter,
  probePrinter,
  resumePrinter,
} from '../../api/printers';
import type { ManagedPrinter } from '../../types';
import { SettingField } from './shared';

const printerStateLabels: Record<number, string> = { 3: 'Idle', 4: 'Printing', 5: 'Stopped' };

// Printer discovery UI (a later phase) will attach to this component; keep it in
// its own file so those props can be added here without touching other cards.
export default function PrintersCard() {
  const toast = useToast();
  const [printers, setPrinters] = useState<ManagedPrinter[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [addMode, setAddMode] = useState<'ip' | 'manual'>('ip');
  const [editId, setEditId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [form, setForm] = useState({ display_name: '', uri: '', description: '', is_network_queue: false, auto_release: false });
  const [editForm, setEditForm] = useState({ display_name: '', uri: '', description: '', auto_release: false });
  // IP mode state
  const [ipAddress, setIpAddress] = useState('');
  const [probeStatus, setProbeStatus] = useState<'idle' | 'probing' | 'reachable' | 'unreachable'>('idle');

  const load = () => listPrinters().then(setPrinters).catch(() => {});

  useEffect(() => { load(); }, []);

  const ipUri = ipAddress ? `ipp://${ipAddress}/ipp` : '';
  const canAddPrinter = form.display_name.trim() !== '' &&
    (addMode === 'manual' || ipAddress.trim() !== '');

  const handleProbe = async () => {
    if (!ipAddress) return;
    setProbeStatus('probing');
    try {
      const result = await probePrinter(ipAddress);
      setProbeStatus(result.reachable ? 'reachable' : 'unreachable');
    } catch {
      setProbeStatus('unreachable');
    }
  };

  const handleAdd = async () => {
    const uri = addMode === 'ip' ? ipUri : form.uri;
    if (!form.display_name) return;
    try {
      await addPrinter({ ...form, uri: uri || undefined });
      resetAdd();
      load();
    } catch { toast.show('Failed to add printer'); }
  };

  const handleUpdate = async (id: number) => {
    try {
      await updatePrinter(id, { ...editForm, uri: editForm.uri || undefined });
      setEditId(null);
      load();
    } catch { toast.show('Failed to update printer'); }
  };

  const handleDelete = async (id: number) => {
    try { await deletePrinter(id); setConfirmDeleteId(null); load(); } catch { toast.show('Failed to delete printer'); }
  };

  const handleDefault = async (id: number) => {
    try { await setDefaultPrinter(id); load(); } catch { toast.show('Failed to set default'); }
  };

  const handleResume = async (id: number) => {
    try { await resumePrinter(id); load(); } catch { toast.show('Failed to resume printer'); }
  };

  const startEdit = (p: ManagedPrinter) => {
    setEditId(p.id);
    setEditForm({ display_name: p.display_name, uri: p.uri, description: p.description || '', auto_release: p.auto_release });
  };

  const resetAdd = () => {
    setShowAdd(false);
    setIpAddress('');
    setProbeStatus('idle');
    setForm({ display_name: '', uri: '', description: '', is_network_queue: false, auto_release: false });
  };

  return (
    <Card title="Printers">
      <div className="space-y-3">
        {printers.length === 0 && <p className="text-sm text-gray-500">No printers configured yet.</p>}
        {printers.map((p) => (
          <div key={p.id} className="p-3 rounded-lg border border-gray-200 dark:border-gray-700 space-y-2">
            {editId === p.id ? (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <SettingField label="Display Name" value={editForm.display_name} onChange={(v) => setEditForm((f) => ({ ...f, display_name: v }))} />
                  {!p.is_network_queue && <SettingField label="URI" value={editForm.uri} onChange={(v) => setEditForm((f) => ({ ...f, uri: v }))} placeholder="ipp://10.0.0.1/ipp" />}
                  <SettingField label="Description" value={editForm.description} onChange={(v) => setEditForm((f) => ({ ...f, description: v }))} />
                  <div className="self-center">
                    <Toggle checked={editForm.auto_release} onChange={(v) => setEditForm((f) => ({ ...f, auto_release: v }))} label="Auto-release" />
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => handleUpdate(p.id)}>Save</Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditId(null)}>Cancel</Button>
                </div>
              </div>
            ) : (
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{p.display_name}</span>
                    {p.is_default && <span className="text-xs bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400 px-1.5 py-0.5 rounded-full font-medium">Default</span>}
                    {p.is_network_queue && <span className="text-xs bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 px-1.5 py-0.5 rounded-full font-medium">Network Queue</span>}
                    {p.auto_release && <span className="text-xs bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-400 px-1.5 py-0.5 rounded-full font-medium">Auto-release</span>}
                    <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${p.cups_status.state === 3 ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400' : p.cups_status.state === 4 ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300' : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400'}`}>
                      {printerStateLabels[p.cups_status.state] || 'Unknown'}
                    </span>
                  </div>
                  {p.uri && <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{p.uri}</div>}
                  {p.description && <div className="text-xs text-gray-400 dark:text-gray-500">{p.description}</div>}
                </div>
                <div className="flex gap-1 ml-2 flex-shrink-0 items-center">
                  {p.cups_status.state === 5 && (
                    <Button size="sm" variant="secondary" onClick={() => handleResume(p.id)}>Resume</Button>
                  )}
                  {!p.is_default && !p.is_network_queue && (
                    <Button size="sm" variant="ghost" onClick={() => handleDefault(p.id)}>Set Default</Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => startEdit(p)}>Edit</Button>
                  {confirmDeleteId === p.id ? (
                    <>
                      <span className="text-xs text-gray-600 dark:text-gray-400">Delete?</span>
                      <Button size="sm" variant="danger" onClick={() => handleDelete(p.id)}>Yes</Button>
                      <Button size="sm" variant="ghost" onClick={() => setConfirmDeleteId(null)}>No</Button>
                    </>
                  ) : (
                    <Button size="sm" variant="danger" onClick={() => setConfirmDeleteId(p.id)}>Delete</Button>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}

        {showAdd ? (
          <div className="p-3 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/30 space-y-3">
            {/* Mode tabs */}
            <div className="flex gap-1 text-xs">
              {(['ip', 'manual'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setAddMode(m)}
                  className={`px-3 py-1 rounded-full font-medium ${addMode === m ? 'bg-blue-600 text-white' : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'}`}
                >
                  {m === 'ip' ? 'IP Address' : 'Manual'}
                </button>
              ))}
            </div>

            {addMode === 'ip' && (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">IP Address</label>
                    <div className="flex gap-1">
                      <input
                        type="text"
                        value={ipAddress}
                        onChange={(e) => { setIpAddress(e.target.value); setProbeStatus('idle'); }}
                        placeholder="192.168.1.100"
                        className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
                      />
                      <Button size="sm" onClick={handleProbe} disabled={!ipAddress || probeStatus === 'probing'}>
                        {probeStatus === 'probing' ? '…' : 'Test'}
                      </Button>
                    </div>
                    {probeStatus === 'reachable' && <p className="text-xs text-green-600 dark:text-green-400 mt-0.5">Printer reachable</p>}
                    {probeStatus === 'unreachable' && <p className="text-xs text-red-600 dark:text-red-400 mt-0.5">Not reachable — check IP and network</p>}
                  </div>
                  <SettingField label="Printer Name" value={form.display_name} onChange={(v) => setForm((f) => ({ ...f, display_name: v }))} placeholder="Brother DCP-L2540DW" />
                </div>
                {ipUri && (
                  <p className="text-xs text-gray-500 dark:text-gray-400">URI: <span className="font-mono">{ipUri}</span></p>
                )}
              </div>
            )}

            {addMode === 'manual' && (
              <div className="grid grid-cols-2 gap-2">
                <SettingField label="Display Name" value={form.display_name} onChange={(v) => setForm((f) => ({ ...f, display_name: v }))} placeholder="Brother DCP-L2540DW" />
                <SettingField label="URI" value={form.uri} onChange={(v) => setForm((f) => ({ ...f, uri: v }))} placeholder="ipp://10.0.0.1/ipp" />
                <SettingField label="Description" value={form.description} onChange={(v) => setForm((f) => ({ ...f, description: v }))} />
              </div>
            )}

            <div className="flex gap-4">
              <Toggle checked={form.is_network_queue} onChange={(v) => setForm((f) => ({ ...f, is_network_queue: v }))} label="Network queue only" />
              <Toggle checked={form.auto_release} onChange={(v) => setForm((f) => ({ ...f, auto_release: v }))} label="Auto-release jobs" />
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={handleAdd} disabled={!canAddPrinter}>Add Printer</Button>
              <Button size="sm" variant="ghost" onClick={resetAdd}>Cancel</Button>
            </div>
          </div>
        ) : (
          <Button size="sm" variant="secondary" onClick={() => setShowAdd(true)}>+ Add Printer</Button>
        )}
      </div>
    </Card>
  );
}
