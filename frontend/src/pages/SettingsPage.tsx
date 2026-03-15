import { useState, useEffect } from 'react';
import Card from '../components/common/Card';
import Button from '../components/common/Button';
import { useToast } from '../components/common/Toast';
import api from '../api/client';
import { listProviders, disconnectProvider, getAuthorizeUrl } from '../api/cloud';
import {
  listPrinters,
  addPrinter,
  updatePrinter,
  deletePrinter,
  setDefaultPrinter,
  probePrinter,
  resumePrinter,
} from '../api/printers';
import {
  listScanners,
  addScanner,
  updateScanner,
  deleteScanner,
  setDefaultScanner,
  discoverScanners,
  probeScanner,
  testScanner,
  registerBrscan4,
} from '../api/scanners';
import type { ScannerTestResult } from '../api/scanners';
import type { APIToken, CloudProvider, ManagedPrinter, ManagedScanner, DiscoveredDevice } from '../types';

const providerLabels: Record<string, string> = {
  gdrive: 'Google Drive',
  dropbox: 'Dropbox',
  onedrive: 'OneDrive',
  webdav: 'WebDAV / Nextcloud',
};

type AppSettings = Record<string, string | number | boolean>;

function SettingField({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
}: {
  label: string;
  value: string | number | boolean;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
}) {
  if (type === 'checkbox') {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(String(e.target.checked))}
          className="rounded border-gray-300 dark:border-gray-600"
        />
        <span className="font-medium text-gray-700 dark:text-gray-300">{label}</span>
      </label>
    );
  }
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{label}</label>
      <input
        type={type}
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
      />
    </div>
  );
}

const printerStateLabels: Record<number, string> = { 3: 'Idle', 4: 'Printing', 5: 'Stopped' };

function PrintersCard() {
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
                  <label className="flex items-center gap-2 text-sm self-center">
                    <input type="checkbox" checked={editForm.auto_release} onChange={(e) => setEditForm((f) => ({ ...f, auto_release: e.target.checked }))} className="rounded border-gray-300 dark:border-gray-600" />
                    <span className="font-medium text-gray-700 dark:text-gray-300">Auto-release</span>
                  </label>
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
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.is_network_queue} onChange={(e) => setForm((f) => ({ ...f, is_network_queue: e.target.checked }))} className="rounded border-gray-300 dark:border-gray-600" />
                <span className="font-medium text-gray-700 dark:text-gray-300">Network queue only (no physical printer)</span>
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.auto_release} onChange={(e) => setForm((f) => ({ ...f, auto_release: e.target.checked }))} className="rounded border-gray-300 dark:border-gray-600" />
                <span className="font-medium text-gray-700 dark:text-gray-300">Auto-release jobs</span>
              </label>
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

function ScannersCard() {
  const toast = useToast();
  const [scanners, setScanners] = useState<ManagedScanner[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [addMode, setAddMode] = useState<'manual' | 'ip' | 'discover' | 'brother'>('manual');
  const [editId, setEditId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [discovered, setDiscovered] = useState<DiscoveredDevice[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [form, setForm] = useState({ name: '', device: '', description: '', auto_deliver: false });
  const [editForm, setEditForm] = useState({ name: '', device: '', description: '', auto_deliver: false });
  // IP mode state
  const [ipAddress, setIpAddress] = useState('');
  const [probeStatus, setProbeStatus] = useState<'idle' | 'probing' | 'reachable' | 'unreachable'>('idle');
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probeDevice, setProbeDevice] = useState<string | null>(null);
  const [probeAirscanUrl, setProbeAirscanUrl] = useState<string | null>(null);
  const [probeProtocol, setProbeProtocol] = useState<string | null>(null);
  // Brother mode state
  const [brotherModel, setBrotherModel] = useState('');
  const [brotherDevice, setBrotherDevice] = useState('');
  const [brotherRegisterStatus, setBrotherRegisterStatus] = useState<'idle' | 'registering' | 'ok' | 'error'>('idle');
  const [brotherError, setBrotherError] = useState<string | null>(null);
  // Test existing scanner state
  const [testResults, setTestResults] = useState<Record<number, ScannerTestResult | 'testing' | 'error'>>({});

  const load = () => listScanners().then(setScanners).catch(() => {});

  useEffect(() => { load(); }, []);

  const ipDevice = ipAddress
    ? `airscan:e:${form.name || 'Scanner'}:http://${ipAddress}/eSCL`
    : '';
  const canAddScanner = form.name.trim() !== '' && (
    addMode === 'ip' ? ipAddress.trim() !== '' :
    addMode === 'brother' ? brotherDevice.trim() !== '' :
    form.device.trim() !== ''
  );

  const handleProbe = async () => {
    if (!ipAddress) return;
    setProbeStatus('probing');
    setProbeError(null);
    setProbeDevice(null);
    setProbeAirscanUrl(null);
    setProbeProtocol(null);
    try {
      const result = await probeScanner(ipAddress);
      if (result.reachable) {
        setProbeStatus('reachable');
        setProbeDevice(result.device);
        setProbeAirscanUrl(result.airscan_url);
        setProbeProtocol(result.protocol);
        if (result.make_model && !form.name) {
          setForm((f) => ({ ...f, name: result.make_model! }));
        }
      } else {
        setProbeStatus('unreachable');
        setProbeError(result.error ?? null);
      }
    } catch {
      setProbeStatus('unreachable');
    }
  };

  const handleBrotherRegister = async () => {
    setBrotherRegisterStatus('registering');
    setBrotherError(null);
    try {
      const result = await registerBrscan4(form.name, brotherModel, ipAddress);
      if (result.device) {
        setBrotherDevice(result.device);
        setBrotherRegisterStatus('ok');
      } else {
        setBrotherRegisterStatus('error');
        setBrotherError(result.error ?? 'Unknown error');
      }
    } catch {
      setBrotherRegisterStatus('error');
      setBrotherError('Request failed');
    }
  };

  const handleTestScanner = async (id: number) => {
    setTestResults((r) => ({ ...r, [id]: 'testing' }));
    try {
      const result = await testScanner(id);
      setTestResults((r) => ({ ...r, [id]: result }));
    } catch {
      setTestResults((r) => ({ ...r, [id]: 'error' }));
    }
  };

  const handleDiscover = async () => {
    setDiscovering(true);
    try {
      const devices = await discoverScanners();
      setDiscovered(devices);
    } catch { toast.show('Discovery failed'); }
    finally { setDiscovering(false); }
  };

  const handleAdd = async () => {
    const device = addMode === 'ip' ? (probeDevice || ipDevice)
      : addMode === 'brother' ? brotherDevice
      : form.device;
    if (!form.name || !device) return;
    const extra = addMode === 'brother'
      ? { post_scan_config: { brother_model: brotherModel, brother_ip: ipAddress } }
      : addMode === 'ip' && probeAirscanUrl && probeProtocol
      ? { post_scan_config: { airscan_url: probeAirscanUrl, airscan_protocol: probeProtocol } }
      : {};
    try {
      await addScanner({ ...form, device, ...extra });
      setForm({ name: '', device: '', description: '', auto_deliver: false });
      setIpAddress('');
      setProbeStatus('idle');
      setShowAdd(false);
      setDiscovered([]);
      load();
    } catch { toast.show('Failed to add scanner'); }
  };

  const handleUpdate = async (id: number) => {
    try {
      await updateScanner(id, editForm);
      setEditId(null);
      load();
    } catch { toast.show('Failed to update scanner'); }
  };

  const handleDelete = async (id: number) => {
    try { await deleteScanner(id); setConfirmDeleteId(null); load(); } catch { toast.show('Failed to delete scanner'); }
  };

  const handleDefault = async (id: number) => {
    try { await setDefaultScanner(id); load(); } catch { toast.show('Failed to set default'); }
  };

  const startEdit = (s: ManagedScanner) => {
    setEditId(s.id);
    setEditForm({ name: s.name, device: s.device, description: s.description || '', auto_deliver: s.auto_deliver });
  };

  const resetAdd = () => {
    setShowAdd(false);
    setDiscovered([]);
    setIpAddress('');
    setProbeStatus('idle');
    setProbeError(null);
    setProbeDevice(null);
    setProbeAirscanUrl(null);
    setProbeProtocol(null);
    setBrotherModel('');
    setBrotherDevice('');
    setBrotherRegisterStatus('idle');
    setBrotherError(null);
    setForm({ name: '', device: '', description: '', auto_deliver: false });
  };

  return (
    <Card title="Scanners">
      <div className="space-y-3">
        {scanners.length === 0 && <p className="text-sm text-gray-500">No scanners configured yet.</p>}
        {scanners.map((s) => (
          <div key={s.id} className="p-3 rounded-lg border border-gray-200 dark:border-gray-700 space-y-2">
            {editId === s.id ? (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <SettingField label="Name" value={editForm.name} onChange={(v) => setEditForm((f) => ({ ...f, name: v }))} />
                  <SettingField label="SANE Device" value={editForm.device} onChange={(v) => setEditForm((f) => ({ ...f, device: v }))} placeholder="airscan:w:Brother DCP-L2540DW" />
                  <SettingField label="Description" value={editForm.description} onChange={(v) => setEditForm((f) => ({ ...f, description: v }))} />
                  <label className="flex items-center gap-2 text-sm self-center">
                    <input type="checkbox" checked={editForm.auto_deliver} onChange={(e) => setEditForm((f) => ({ ...f, auto_deliver: e.target.checked }))} className="rounded border-gray-300 dark:border-gray-600" />
                    <span className="font-medium text-gray-700 dark:text-gray-300">Auto-deliver</span>
                  </label>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => handleUpdate(s.id)}>Save</Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditId(null)}>Cancel</Button>
                </div>
              </div>
            ) : (
              <>
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{s.name}</span>
                      {s.is_default && <span className="text-xs bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400 px-1.5 py-0.5 rounded-full font-medium">Default</span>}
                      {s.auto_deliver && <span className="text-xs bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400 px-1.5 py-0.5 rounded-full font-medium">Auto-deliver</span>}
                    </div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 font-mono">{s.device}</div>
                    {s.description && <div className="text-xs text-gray-400 dark:text-gray-500">{s.description}</div>}
                  </div>
                  <div className="flex gap-1 ml-2 flex-shrink-0 items-center">
                    {!s.is_default && (
                      <Button size="sm" variant="ghost" onClick={() => handleDefault(s.id)}>Set Default</Button>
                    )}
                    <Button size="sm" variant="ghost" onClick={() => handleTestScanner(s.id)}>
                      {testResults[s.id] === 'testing' ? '…' : 'Test'}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => startEdit(s)}>Edit</Button>
                    {confirmDeleteId === s.id ? (
                      <>
                        <span className="text-xs text-gray-600 dark:text-gray-400">Delete?</span>
                        <Button size="sm" variant="danger" onClick={() => handleDelete(s.id)}>Yes</Button>
                        <Button size="sm" variant="ghost" onClick={() => setConfirmDeleteId(null)}>No</Button>
                      </>
                    ) : (
                      <Button size="sm" variant="danger" onClick={() => setConfirmDeleteId(s.id)}>Delete</Button>
                    )}
                  </div>
                </div>
                {testResults[s.id] && testResults[s.id] !== 'testing' && (
                  <div className="mt-1 text-xs space-y-0.5 border-t border-gray-100 dark:border-gray-800 pt-1.5">
                    {testResults[s.id] === 'error' ? (
                      <span className="text-red-600 dark:text-red-400">Test request failed</span>
                    ) : (
                      <>
                        {(() => {
                          const r = testResults[s.id] as ScannerTestResult;
                          return (
                            <>
                              <div className="flex items-center gap-1.5">
                                <span className={r.escl_ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                                  {r.escl_ok ? '✓' : '✗'} eSCL
                                </span>
                                {!r.escl_ok && r.escl_error && (
                                  <span className="text-gray-500 dark:text-gray-400 truncate">{r.escl_error}</span>
                                )}
                                {r.escl_ok && r.make_model && (
                                  <span className="text-gray-500 dark:text-gray-400">— {r.make_model}</span>
                                )}
                              </div>
                              <div className="flex items-center gap-1.5">
                                <span className={r.sane_ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                                  {r.sane_ok ? '✓' : '✗'} SANE
                                </span>
                                {!r.sane_ok && r.sane_error && (
                                  <span className="text-gray-500 dark:text-gray-400 truncate">{r.sane_error}</span>
                                )}
                              </div>
                            </>
                          );
                        })()}
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        ))}

        {showAdd ? (
          <div className="p-3 rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/30 space-y-3">
            {/* Mode tabs */}
            <div className="flex gap-1 text-xs">
              {(['ip', 'brother', 'manual', 'discover'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setAddMode(m)}
                  className={`px-3 py-1 rounded-full font-medium ${addMode === m ? 'bg-blue-600 text-white' : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 border border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'}`}
                >
                  {m === 'ip' ? 'IP Address' : m === 'brother' ? 'Brother' : m === 'manual' ? 'Manual' : 'Discover'}
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
                    {probeStatus === 'reachable' && <p className="text-xs text-green-600 dark:text-green-400 mt-0.5">Scanner reachable</p>}
                    {probeStatus === 'unreachable' && (
                      <p className="text-xs text-red-600 dark:text-red-400 mt-0.5">
                        Not reachable{probeError ? `: ${probeError}` : ' — check IP and network'}
                      </p>
                    )}
                  </div>
                  <SettingField label="Name" value={form.name} onChange={(v) => setForm((f) => ({ ...f, name: v }))} placeholder="Brother DCP-L2540DW" />
                </div>
                {(probeDevice || ipDevice) && (
                  <p className="text-xs text-gray-500 dark:text-gray-400">Device string: <span className="font-mono">{probeDevice || ipDevice}</span></p>
                )}
              </div>
            )}

            {addMode === 'manual' && (
              <div className="grid grid-cols-2 gap-2">
                <SettingField label="Name" value={form.name} onChange={(v) => setForm((f) => ({ ...f, name: v }))} placeholder="Brother DCP-L2540DW" />
                <SettingField label="SANE Device String" value={form.device} onChange={(v) => setForm((f) => ({ ...f, device: v }))} placeholder="airscan:w:Brother DCP-L2540DW" />
              </div>
            )}

            {addMode === 'discover' && (
              <div className="space-y-2">
                <div className="flex gap-2">
                  <SettingField label="Name" value={form.name} onChange={(v) => setForm((f) => ({ ...f, name: v }))} placeholder="Brother DCP-L2540DW" />
                  <div className="self-end">
                    <Button size="sm" variant="secondary" onClick={handleDiscover} disabled={discovering}>
                      {discovering ? 'Scanning…' : 'Scan Network'}
                    </Button>
                  </div>
                </div>
                {discovered.length > 0 && (
                  <div>
                    <p className="text-xs text-gray-600 dark:text-gray-400 mb-1">Found devices (click to select):</p>
                    <div className="space-y-1">
                      {discovered.map((d) => (
                        <button
                          key={d.device}
                          onClick={() => setForm((f) => ({ ...f, device: d.device, name: f.name || d.description }))}
                          className={`block w-full text-left text-xs p-2 rounded border hover:bg-gray-50 dark:hover:bg-gray-700 ${form.device === d.device ? 'border-blue-400 bg-blue-50 dark:bg-blue-950/30' : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'}`}
                        >
                          <span className="font-mono dark:text-gray-300">{d.device}</span>
                          {d.description && <span className="text-gray-500 dark:text-gray-400 ml-1">— {d.description}</span>}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {discovered.length === 0 && !discovering && (
                  <p className="text-xs text-gray-500 dark:text-gray-400">Click "Scan Network" to find scanners via mDNS.</p>
                )}
              </div>
            )}

            {addMode === 'brother' && (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <SettingField label="Scanner Name" value={form.name}
                    onChange={(v) => setForm((f) => ({ ...f, name: v }))}
                    placeholder="Brother DCP-L2540DW" />
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Model</label>
                    <input
                      type="text"
                      value={brotherModel}
                      onChange={(e) => setBrotherModel(e.target.value)}
                      placeholder="DCP-L2540DW"
                      className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
                    />
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Exact Brother model name (e.g. DCP-L2540DW)</p>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">IP Address</label>
                    <div className="flex gap-1">
                      <input
                        type="text"
                        value={ipAddress}
                        onChange={(e) => setIpAddress(e.target.value)}
                        placeholder="10.10.77.50"
                        className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
                      />
                      <Button size="sm" onClick={handleBrotherRegister}
                        disabled={!ipAddress || !brotherModel || !form.name || brotherRegisterStatus === 'registering'}>
                        {brotherRegisterStatus === 'registering' ? '...' : 'Register'}
                      </Button>
                    </div>
                  </div>
                </div>
                {brotherRegisterStatus === 'ok' && (
                  <p className="text-xs text-green-600 dark:text-green-400">
                    Registered — device: <span className="font-mono">{brotherDevice}</span>
                  </p>
                )}
                {brotherRegisterStatus === 'error' && (
                  <p className="text-xs text-red-600 dark:text-red-400">{brotherError}</p>
                )}
              </div>
            )}

            <div className="grid grid-cols-2 gap-2">
              <SettingField label="Description (optional)" value={form.description} onChange={(v) => setForm((f) => ({ ...f, description: v }))} />
              <label className="flex items-center gap-2 text-sm self-center">
                <input type="checkbox" checked={form.auto_deliver} onChange={(e) => setForm((f) => ({ ...f, auto_deliver: e.target.checked }))} className="rounded border-gray-300 dark:border-gray-600" />
                <span className="font-medium text-gray-700 dark:text-gray-300">Auto-deliver scans</span>
              </label>
            </div>

            <div className="flex gap-2">
              <Button size="sm" onClick={handleAdd} disabled={!canAddScanner}>Add Scanner</Button>
              <Button size="sm" variant="ghost" onClick={resetAdd}>Cancel</Button>
            </div>
          </div>
        ) : (
          <Button size="sm" variant="secondary" onClick={() => setShowAdd(true)}>+ Add Scanner</Button>
        )}
      </div>
    </Card>
  );
}

interface WebhookItem {
  id: number;
  name: string;
  url: string;
  events: string[];
  enabled: boolean;
  created_at: string;
}

function WebhooksCard() {
  const toast = useToast();
  const [hooks, setHooks] = useState<WebhookItem[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ name: '', url: '', secret: '', events: [] as string[] });

  const load = () => {
    api.get('/webhooks').then(({ data }) => setHooks(data)).catch(() => {});
    api.get('/webhooks/events').then(({ data }) => setEvents(data)).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    if (!form.name || !form.url || form.events.length === 0) return;
    try {
      await api.post('/webhooks', form);
      setForm({ name: '', url: '', secret: '', events: [] });
      setShowAdd(false);
      load();
    } catch { toast.show('Failed to create webhook', 'error'); }
  };

  const toggleEnabled = async (hook: WebhookItem) => {
    try {
      await api.put(`/webhooks/${hook.id}`, { ...hook, enabled: !hook.enabled });
      load();
    } catch { toast.show('Failed to update webhook', 'error'); }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.delete(`/webhooks/${id}`);
      load();
    } catch { toast.show('Failed to delete webhook', 'error'); }
  };

  const toggleEvent = (evt: string) => {
    setForm((f) => ({
      ...f,
      events: f.events.includes(evt) ? f.events.filter((e) => e !== evt) : [...f.events, evt],
    }));
  };

  return (
    <Card title="Webhooks">
      <div className="space-y-4">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Send HTTP POST notifications when events occur. Payloads are signed with HMAC-SHA256 if a secret is set.
        </p>
        {hooks.length === 0 && !showAdd && (
          <p className="text-gray-500 text-sm">No webhooks configured.</p>
        )}
        {hooks.map((hook) => (
          <div key={hook.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{hook.name}</div>
              <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{hook.url}</div>
              <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">{hook.events.join(', ')}</div>
            </div>
            <div className="flex items-center gap-2 ml-3">
              <button
                onClick={() => toggleEnabled(hook)}
                className={`px-2 py-1 text-xs rounded ${hook.enabled ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}`}
              >{hook.enabled ? 'On' : 'Off'}</button>
              <Button size="sm" variant="danger" onClick={() => handleDelete(hook.id)}>Delete</Button>
            </div>
          </div>
        ))}
        {showAdd ? (
          <div className="space-y-3 p-3 border border-gray-200 dark:border-gray-700 rounded-lg">
            <input
              type="text"
              placeholder="Webhook name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
            />
            <input
              type="url"
              placeholder="https://example.com/webhook"
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
            />
            <input
              type="text"
              placeholder="Signing secret (optional)"
              value={form.secret}
              onChange={(e) => setForm({ ...form, secret: e.target.value })}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
            />
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Events</label>
              <div className="flex flex-wrap gap-2">
                {events.map((evt) => (
                  <button
                    key={evt}
                    onClick={() => toggleEvent(evt)}
                    className={`px-2 py-1 text-xs rounded border ${form.events.includes(evt) ? 'bg-blue-50 border-blue-300 text-blue-700 dark:bg-blue-950 dark:border-blue-700 dark:text-blue-300' : 'border-gray-300 text-gray-500 dark:border-gray-600 dark:text-gray-400'}`}
                  >{evt}</button>
                ))}
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <Button size="sm" variant="secondary" onClick={() => setShowAdd(false)}>Cancel</Button>
              <Button size="sm" onClick={handleCreate}>Create</Button>
            </div>
          </div>
        ) : (
          <Button size="sm" onClick={() => setShowAdd(true)}>Add Webhook</Button>
        )}
      </div>
    </Card>
  );
}

export default function SettingsPage() {
  const [appSettings, setAppSettings] = useState<AppSettings>({});
  const [tokens, setTokens] = useState<APIToken[]>([]);
  const [newTokenName, setNewTokenName] = useState('');
  const [newTokenPermissions, setNewTokenPermissions] = useState<string[]>([]);
  const [newTokenExpiry, setNewTokenExpiry] = useState<number | null>(null);
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [cloudProviders, setCloudProviders] = useState<CloudProvider[]>([]);
  const [webhookInfo, setWebhookInfo] = useState<{ webhook_url: string; configured: boolean } | null>(null);
  const [webhookSecret, setWebhookSecret] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<Record<string, 'saving' | 'saved' | 'error'>>({});
  const [showWebdav, setShowWebdav] = useState(false);
  const [webdavForm, setWebdavForm] = useState({ url: '', username: '', password: '' });

  useEffect(() => {
    api.get('/settings').then(({ data }) => setAppSettings(data)).catch(() => {});
    api.get('/auth/tokens').then(({ data }) => setTokens(data)).catch(() => {});
    listProviders().then(setCloudProviders).catch(() => {});
    api.get('/email/webhook-info').then(({ data }) => setWebhookInfo(data)).catch(() => {});
  }, []);

  const set = (key: string) => (value: string) => {
    setAppSettings((prev) => ({ ...prev, [key]: value }));
  };

  const saveSection = async (section: string, keys: string[]) => {
    setSaveStatus((s) => ({ ...s, [section]: 'saving' }));
    try {
      const payload = Object.fromEntries(keys.map((k) => [k, appSettings[k]]));
      await api.put('/settings', payload);
      setSaveStatus((s) => ({ ...s, [section]: 'saved' }));
      setTimeout(() => setSaveStatus((s) => ({ ...s, [section]: undefined as unknown as 'saved' })), 2000);
    } catch {
      setSaveStatus((s) => ({ ...s, [section]: 'error' }));
    }
  };

  const SaveButton = ({ section, keys }: { section: string; keys: string[] }) => {
    const status = saveStatus[section];
    return (
      <Button
        onClick={() => saveSection(section, keys)}
        disabled={status === 'saving'}
        variant={status === 'error' ? 'danger' : 'primary'}
      >
        {status === 'saving' ? 'Saving…' : status === 'saved' ? 'Saved ✓' : status === 'error' ? 'Error' : 'Save'}
      </Button>
    );
  };

  const toast = useToast();

  const allPermissions = ['print', 'scan', 'files', 'admin', 'email'] as const;
  const permissionLabels: Record<string, string> = {
    print: 'Print', scan: 'Scan', files: 'Files', admin: 'Admin', email: 'Email',
  };

  const togglePermission = (perm: string) => {
    setNewTokenPermissions((prev) =>
      prev.includes(perm) ? prev.filter((p) => p !== perm) : [...prev, perm]
    );
  };

  const createToken = async () => {
    if (!newTokenName || newTokenPermissions.length === 0) return;
    try {
      const { data } = await api.post('/auth/tokens', {
        name: newTokenName,
        permissions: newTokenPermissions,
        expires_in_days: newTokenExpiry,
      });
      setCreatedToken(data.token);
      setNewTokenName('');
      setNewTokenPermissions([]);
      setNewTokenExpiry(null);
      const { data: refreshed } = await api.get('/auth/tokens');
      setTokens(refreshed);
    } catch {
      toast.show('Failed to create token');
    }
  };

  const revokeToken = async (id: string) => {
    try {
      await api.delete(`/auth/tokens/${id}`);
      setTokens(tokens.filter((t) => t.id !== id));
    } catch {
      toast.show('Failed to revoke token');
    }
  };

  const handleDisconnectCloud = async (id: number) => {
    try {
      await disconnectProvider(id);
      setCloudProviders(cloudProviders.filter((p) => p.id !== id));
    } catch {
      toast.show('Failed to disconnect provider');
    }
  };

  const handleConnectWebdav = async () => {
    if (!webdavForm.url || !webdavForm.username || !webdavForm.password) return;
    try {
      await api.post('/webdav/connect', webdavForm);
      setShowWebdav(false);
      setWebdavForm({ url: '', username: '', password: '' });
      listProviders().then(setCloudProviders).catch(() => {});
      toast.show('WebDAV connected', 'success');
    } catch {
      toast.show('Failed to connect — check URL and credentials');
    }
  };

  const generateWebhookSecret = async () => {
    try {
      const { data } = await api.post('/email/webhook-secret');
      setWebhookSecret(data.secret);
      setWebhookInfo({ webhook_url: data.webhook_url, configured: true });
    } catch {
      toast.show('Failed to generate webhook secret');
    }
  };

  const testSmtp = async () => {
    try {
      await api.post('/email/test');
      toast.show('SMTP connection successful', 'success');
    } catch {
      toast.show('SMTP connection failed');
    }
  };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Settings</h2>

      <PrintersCard />
      <ScannersCard />

      {/* Network Services */}
      <Card title="Network Services">
        <div className="space-y-3">
          <p className="text-sm text-gray-600 dark:text-gray-400">eSCL (AirScan) enables network scanner discovery by macOS and iOS.</p>
          <SettingField label="Enable eSCL Scanner (AirScan)" value={appSettings['escl_enabled'] ?? true} onChange={set('escl_enabled')} type="checkbox" />
          <div className="flex justify-end">
            <SaveButton section="network" keys={['escl_enabled']} />
          </div>
        </div>
      </Card>

      {/* Storage */}
      <Card title="Storage">
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <SettingField label="Scan Output Directory" value={appSettings['scan_dir'] ?? ''} onChange={set('scan_dir')} placeholder="/app/data/scans" />
            <SettingField label="Upload Directory" value={appSettings['upload_dir'] ?? ''} onChange={set('upload_dir')} placeholder="/app/data/uploads" />
            <SettingField label="Max Upload Size (MB)" value={appSettings['max_upload_size_mb'] ?? 50} onChange={set('max_upload_size_mb')} type="number" />
            <SettingField label="Scan Retention (days)" value={appSettings['scan_retention_days'] ?? 7} onChange={set('scan_retention_days')} type="number" />
            <SettingField label="Print Retention (days)" value={appSettings['print_retention_days'] ?? 30} onChange={set('print_retention_days')} type="number" />
          </div>
          <div className="flex justify-end">
            <SaveButton section="storage" keys={['scan_dir', 'upload_dir', 'max_upload_size_mb', 'scan_retention_days', 'print_retention_days']} />
          </div>
        </div>
      </Card>

      {/* Application */}
      <Card title="Application">
        <div className="space-y-3">
          <SettingField label="Base URL" value={appSettings['base_url'] ?? ''} onChange={set('base_url')} placeholder="https://papyrus.example.com" />
          <SettingField label="Development Mode" value={appSettings['dev_mode'] ?? false} onChange={set('dev_mode')} type="checkbox" />
          <SettingField label="Require Release PIN" value={appSettings['require_release_pin'] ?? false} onChange={set('require_release_pin')} type="checkbox" />
          <p className="text-xs text-gray-500 dark:text-gray-400">When enabled, uploaded jobs get a randomly generated PIN required at release time.</p>
          <div className="flex justify-end">
            <SaveButton section="application" keys={['base_url', 'dev_mode', 'require_release_pin']} />
          </div>
        </div>
      </Card>

      {/* Email / SMTP */}
      <Card title="Email (SMTP)">
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <SettingField label="SMTP Host" value={appSettings['smtp_host'] ?? ''} onChange={set('smtp_host')} placeholder="smtp.example.com" />
            <SettingField label="Port" value={appSettings['smtp_port'] ?? 587} onChange={set('smtp_port')} type="number" />
            <SettingField label="Username" value={appSettings['smtp_user'] ?? ''} onChange={set('smtp_user')} />
            <SettingField label="Password" value={appSettings['smtp_password'] ?? ''} onChange={set('smtp_password')} type="password" />
          </div>
          <SettingField label="From Address" value={appSettings['smtp_from'] ?? ''} onChange={set('smtp_from')} placeholder="papyrus@example.com" />
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={testSmtp}>Test Connection</Button>
            <SaveButton section="smtp" keys={['smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_from']} />
          </div>
        </div>
      </Card>

      {/* Email Webhook */}
      <Card title="Email Webhook">
        <div className="space-y-3">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            External services can forward email attachments to Papyrus for automatic printing.
          </p>
          <SettingField label="Rate Limit (requests/min/IP)" value={appSettings['email_webhook_rate_limit'] ?? 10} onChange={set('email_webhook_rate_limit')} type="number" />
          <div className="flex justify-end">
            <SaveButton section="webhook-rate" keys={['email_webhook_rate_limit']} />
          </div>
          {webhookInfo && (
            <div className="space-y-2 pt-2 border-t border-gray-100 dark:border-gray-800">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Webhook URL</label>
                <code className="block text-xs bg-gray-100 dark:bg-gray-800 dark:text-gray-300 p-2 rounded break-all">{webhookInfo.webhook_url}</code>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-600 dark:text-gray-400">Secret configured:</span>
                <span className={`text-sm font-medium ${webhookInfo.configured ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
                  {webhookInfo.configured ? 'Yes' : 'No'}
                </span>
              </div>
            </div>
          )}
          {webhookSecret && (
            <div className="bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-800 rounded-lg p-3">
              <p className="text-sm text-yellow-800 dark:text-yellow-300 font-medium">
                Webhook secret generated! Copy it now &mdash; it won&apos;t be shown again:
              </p>
              <code className="text-xs break-all block mt-1 bg-yellow-100 dark:bg-yellow-900/50 dark:text-yellow-200 p-2 rounded">{webhookSecret}</code>
            </div>
          )}
          <div className="flex justify-end">
            <Button size="sm" onClick={generateWebhookSecret}>
              {webhookInfo?.configured ? 'Regenerate Secret' : 'Generate Secret'}
            </Button>
          </div>
          <div className="text-xs text-gray-500 dark:text-gray-400 space-y-1">
            <p>Usage example:</p>
            <code className="block bg-gray-100 dark:bg-gray-800 dark:text-gray-300 p-2 rounded break-all">
              curl -F &quot;token=YOUR_SECRET&quot; -F &quot;file=@document.pdf&quot; {webhookInfo?.webhook_url || 'https://papyrus.example.com/api/email/receive'}
            </code>
          </div>
        </div>
      </Card>

      {/* Cloud OAuth Credentials */}
      <Card title="Cloud OAuth Credentials">
        <div className="space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Configure OAuth app credentials to enable Google Drive, Dropbox, and OneDrive integration.
          </p>
          <div className="space-y-3">
            <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Google Drive</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <SettingField label="Client ID" value={appSettings['gdrive_client_id'] ?? ''} onChange={set('gdrive_client_id')} />
              <SettingField label="Client Secret" value={appSettings['gdrive_client_secret'] ?? ''} onChange={set('gdrive_client_secret')} type="password" />
            </div>
          </div>
          <div className="space-y-3">
            <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Dropbox</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <SettingField label="App Key" value={appSettings['dropbox_app_key'] ?? ''} onChange={set('dropbox_app_key')} />
              <SettingField label="App Secret" value={appSettings['dropbox_app_secret'] ?? ''} onChange={set('dropbox_app_secret')} type="password" />
            </div>
          </div>
          <div className="space-y-3">
            <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">OneDrive</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <SettingField label="Client ID" value={appSettings['onedrive_client_id'] ?? ''} onChange={set('onedrive_client_id')} />
              <SettingField label="Client Secret" value={appSettings['onedrive_client_secret'] ?? ''} onChange={set('onedrive_client_secret')} type="password" />
            </div>
          </div>
          <div className="flex justify-end">
            <SaveButton section="cloud-creds" keys={['gdrive_client_id', 'gdrive_client_secret', 'dropbox_app_key', 'dropbox_app_secret', 'onedrive_client_id', 'onedrive_client_secret']} />
          </div>
        </div>
      </Card>

      {/* Cloud Storage — connect/disconnect */}
      <Card title="Cloud Storage">
        <div className="space-y-4">
          {cloudProviders.length > 0 && (
            <div className="space-y-2">
              {cloudProviders.map((p) => (
                <div key={p.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700">
                  <div>
                    <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{providerLabels[p.provider] || p.provider}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400">Connected {new Date(p.connected_at).toLocaleDateString()}</div>
                  </div>
                  <Button size="sm" variant="danger" onClick={() => handleDisconnectCloud(p.id)}>Disconnect</Button>
                </div>
              ))}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <a href={getAuthorizeUrl('gdrive')}><Button size="sm" variant="secondary">Connect Google Drive</Button></a>
            <a href={getAuthorizeUrl('dropbox')}><Button size="sm" variant="secondary">Connect Dropbox</Button></a>
            <a href={getAuthorizeUrl('onedrive')}><Button size="sm" variant="secondary">Connect OneDrive</Button></a>
            <Button size="sm" variant="secondary" onClick={() => setShowWebdav(!showWebdav)}>Connect WebDAV</Button>
          </div>
          {showWebdav && (
            <div className="space-y-3 p-3 border border-gray-200 dark:border-gray-700 rounded-lg">
              <input
                type="url"
                placeholder="https://cloud.example.com/remote.php/dav/files/user"
                value={webdavForm.url}
                onChange={(e) => setWebdavForm({ ...webdavForm, url: e.target.value })}
                className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
              />
              <div className="grid grid-cols-2 gap-3">
                <input
                  type="text"
                  placeholder="Username"
                  value={webdavForm.username}
                  onChange={(e) => setWebdavForm({ ...webdavForm, username: e.target.value })}
                  className="rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
                />
                <input
                  type="password"
                  placeholder="Password / App Password"
                  value={webdavForm.password}
                  onChange={(e) => setWebdavForm({ ...webdavForm, password: e.target.value })}
                  className="rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
                />
              </div>
              <div className="flex gap-2 justify-end">
                <Button size="sm" variant="secondary" onClick={() => setShowWebdav(false)}>Cancel</Button>
                <Button size="sm" onClick={handleConnectWebdav}>Connect</Button>
              </div>
            </div>
          )}
        </div>
      </Card>

      {/* OCR */}
      <Card title="OCR / Searchable PDFs">
        <div className="space-y-3">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Apply Tesseract OCR to scanned PDFs to make them searchable. Requires tesseract-ocr and ocrmypdf.
          </p>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={appSettings.ocr_enabled === true || appSettings.ocr_enabled === 'true'}
              onChange={(e) => set('ocr_enabled')(String(e.target.checked))}
              className="rounded border-gray-300 dark:border-gray-600"
            />
            <span className="text-sm text-gray-700 dark:text-gray-300">Enable OCR for auto-deliver scans</span>
          </label>
          <SettingField
            label="OCR Language"
            value={appSettings.ocr_language ?? 'eng'}
            onChange={set('ocr_language')}
            placeholder="eng"
          />
          <div className="flex justify-end">
            <SaveButton section="ocr" keys={['ocr_enabled', 'ocr_language']} />
          </div>
        </div>
      </Card>

      {/* Scan Filename Template */}
      <Card title="Scan Filename Template">
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
            <SaveButton section="scan_template" keys={['scan_filename_template']} />
          </div>
        </div>
      </Card>

      {/* Paperless-ngx */}
      <Card title="Paperless-ngx">
        <div className="space-y-3">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Send scans directly to your Paperless-ngx instance for archiving and OCR.
          </p>
          <SettingField
            label="Paperless URL"
            value={appSettings.paperless_url ?? ''}
            onChange={set('paperless_url')}
            placeholder="https://paperless.example.com"
          />
          <SettingField
            label="API Token"
            value={appSettings.paperless_api_token ?? ''}
            onChange={set('paperless_api_token')}
            type="password"
            placeholder="Token from Paperless admin"
          />
          <div className="flex justify-end">
            <SaveButton section="paperless" keys={['paperless_url', 'paperless_api_token']} />
          </div>
        </div>
      </Card>

      {/* FTP/SFTP */}
      <Card title="FTP / SFTP">
        <div className="space-y-3">
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Upload scans to an FTP or SFTP server. Used as a post-scan delivery target.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <SettingField label="Host" value={appSettings.ftp_host ?? ''} onChange={set('ftp_host')} placeholder="ftp.example.com" />
            <SettingField label="Port" value={appSettings.ftp_port ?? '21'} onChange={set('ftp_port')} placeholder="21" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <SettingField label="Username" value={appSettings.ftp_username ?? ''} onChange={set('ftp_username')} />
            <SettingField label="Password" value={appSettings.ftp_password ?? ''} onChange={set('ftp_password')} type="password" />
          </div>
          <SettingField label="Remote Directory" value={appSettings.ftp_remote_dir ?? '/'} onChange={set('ftp_remote_dir')} placeholder="/" />
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
            <SaveButton section="ftp" keys={['ftp_host', 'ftp_port', 'ftp_username', 'ftp_password', 'ftp_remote_dir', 'ftp_protocol']} />
          </div>
        </div>
      </Card>

      <WebhooksCard />

      {/* Backup / Restore */}
      <Card title="Backup / Restore">
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

      {/* API Tokens */}
      <Card title="API Tokens">
        <div className="space-y-4">
          <div className="space-y-3 p-3 rounded-lg border border-gray-200 dark:border-gray-700">
            <input
              type="text"
              placeholder="Token name"
              value={newTokenName}
              onChange={(e) => setNewTokenName(e.target.value)}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
            />
            <div>
              <p className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">Permissions</p>
              <div className="flex flex-wrap gap-2">
                {allPermissions.map((perm) => (
                  <label key={perm} className="flex items-center gap-1.5 text-sm">
                    <input
                      type="checkbox"
                      checked={newTokenPermissions.includes(perm)}
                      onChange={() => togglePermission(perm)}
                      className="rounded border-gray-300 dark:border-gray-600"
                    />
                    <span className="text-gray-700 dark:text-gray-300">{permissionLabels[perm]}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div>
                <p className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Expires</p>
                <select
                  value={newTokenExpiry ?? ''}
                  onChange={(e) => setNewTokenExpiry(e.target.value ? Number(e.target.value) : null)}
                  className="rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
                >
                  <option value="">Never</option>
                  <option value="7">7 days</option>
                  <option value="30">30 days</option>
                  <option value="90">90 days</option>
                  <option value="365">1 year</option>
                </select>
              </div>
              <div className="self-end">
                <Button size="sm" onClick={createToken} disabled={!newTokenName || newTokenPermissions.length === 0}>Create</Button>
              </div>
            </div>
          </div>
          {createdToken && (
            <div className="bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-800 rounded-lg p-3">
              <p className="text-sm text-yellow-800 dark:text-yellow-300 font-medium">
                Token created! Copy it now &mdash; it won&apos;t be shown again:
              </p>
              <code className="text-xs break-all block mt-1 bg-yellow-100 dark:bg-yellow-900/50 dark:text-yellow-200 p-2 rounded">{createdToken}</code>
            </div>
          )}
          {tokens.length === 0 ? (
            <p className="text-gray-500 text-sm">No API tokens created.</p>
          ) : (
            <div className="space-y-2">
              {tokens.map((token) => (
                <div key={token.id} className="flex items-center justify-between p-3 rounded-lg border border-gray-200 dark:border-gray-700">
                  <div>
                    <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{token.name}</div>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {token.permissions.map((p) => (
                        <span key={p} className="text-xs px-1.5 py-0.5 rounded-full font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">{p}</span>
                      ))}
                    </div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      {token.expires_at ? `Expires: ${new Date(token.expires_at).toLocaleDateString()}` : 'No expiry'}
                      {token.last_used_at && ` · Last used: ${new Date(token.last_used_at).toLocaleDateString()}`}
                    </div>
                  </div>
                  <Button size="sm" variant="danger" onClick={() => revokeToken(token.id)}>Revoke</Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
