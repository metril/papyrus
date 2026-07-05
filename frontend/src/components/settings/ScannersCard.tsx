import { useState, useEffect } from 'react';
import Card from '../common/Card';
import Button from '../common/Button';
import Toggle from '../common/Toggle';
import { useToast } from '../../hooks/useToast';
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
} from '../../api/scanners';
import type { ScannerTestResult } from '../../api/scanners';
import type { ManagedScanner, DiscoveredDevice } from '../../types';
import { SettingField } from './shared';

export default function ScannersCard() {
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
                  <div className="self-center">
                    <Toggle checked={editForm.auto_deliver} onChange={(v) => setEditForm((f) => ({ ...f, auto_deliver: v }))} label="Auto-deliver" />
                  </div>
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
              <div className="self-center">
                <Toggle checked={form.auto_deliver} onChange={(v) => setForm((f) => ({ ...f, auto_deliver: v }))} label="Auto-deliver scans" />
              </div>
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
