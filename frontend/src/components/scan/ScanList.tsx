import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ScanLine } from 'lucide-react';
import { useScans, queryKeys } from '../../api/queries';
import { applyScanEvent } from '../../hooks/useRealtimeBridge';
import {
  getScanDownloadUrl,
  getScanThumbnailUrl,
  deleteScan,
  sendScanToPaperless,
  enhanceScan,
  ocrScan,
  collateScans,
} from '../../api/scanner';
import Button from '../common/Button';
import Toggle from '../common/Toggle';
import FilePreviewModal from '../common/FilePreviewModal';
import Skeleton from '../common/Skeleton';
import EmptyState from '../common/EmptyState';
import ErrorState from '../common/ErrorState';
import EmailScanDialog from './EmailScanDialog';
import CloudSaveDialog from './CloudSaveDialog';
import ScanRow from './ScanRow';
import { useToast } from '../../hooks/useToast';
import type { ScanJob } from '../../types';

function scanMimeType(scan: ScanJob): string {
  if (scan.format === 'pdf') return 'application/pdf';
  return `image/${scan.format}`;
}

export default function ScanList() {
  const queryClient = useQueryClient();
  const scansQuery = useScans();
  const scans = scansQuery.data?.scans ?? [];
  const toast = useToast();
  const [emailScanId, setEmailScanId] = useState<string | null>(null);
  const [cloudScanId, setCloudScanId] = useState<string | null>(null);
  const [previewScan, setPreviewScan] = useState<ScanJob | null>(null);
  const [mergeSelection, setMergeSelection] = useState<Set<string>>(new Set());
  const [enhanceScanId, setEnhanceScanId] = useState<string | null>(null);
  const [enhanceForm, setEnhanceForm] = useState({ brightness: 1.0, contrast: 1.0, rotation: 0, auto_crop: false, deskew: false });
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const defaultEnhanceForm = { brightness: 1.0, contrast: 1.0, rotation: 0, auto_crop: false, deskew: false };

  const invalidateScans = useCallback(
    () => queryClient.invalidateQueries({ queryKey: queryKeys.scans.list() }),
    [queryClient],
  );

  const closeEnhanceDialog = () => {
    setEnhanceScanId(null);
    setEnhanceForm(defaultEnhanceForm);
  };

  // Side-action responses aren't guaranteed to be full scans, so each mutation
  // invalidates the scans list rather than upserting a returned object.
  const deleteMutation = useMutation({
    mutationFn: (scanId: string) => deleteScan(scanId),
    meta: { suppressGlobalError: true },
    onSuccess: (_result, scanId) =>
      applyScanEvent(queryClient, { type: 'scan_deleted', data: { scan_id: scanId } }),
  });

  const paperlessMutation = useMutation({
    mutationFn: (scanId: string) => sendScanToPaperless(scanId),
    meta: { suppressGlobalError: true },
    onSuccess: () => { toast.show('Sent to Paperless-ngx', 'success'); invalidateScans(); },
    onError: () => toast.show('Failed to send to Paperless-ngx'),
  });

  const enhanceMutation = useMutation({
    mutationFn: ({ scanId, form }: { scanId: string; form: typeof enhanceForm }) =>
      enhanceScan(scanId, form),
    meta: { suppressGlobalError: true },
    onSuccess: () => {
      toast.show('Enhancement applied', 'success');
      invalidateScans();
      closeEnhanceDialog();
    },
    onError: () => toast.show('Failed to apply enhancement'),
  });

  const ocrMutation = useMutation({
    mutationFn: (scanId: string) => ocrScan(scanId),
    meta: { suppressGlobalError: true },
    onSuccess: () => { toast.show('OCR applied successfully', 'success'); invalidateScans(); },
    onError: () => toast.show('Failed to apply OCR'),
  });

  const convertMutation = useMutation({
    mutationFn: (scanId: string) => collateScans([scanId]),
    meta: { suppressGlobalError: true },
    onSuccess: () => { toast.show('Converted to PDF', 'success'); invalidateScans(); },
    onError: () => toast.show('Failed to convert to PDF'),
  });

  const mergeMutation = useMutation({
    mutationFn: (scanIds: string[]) => collateScans(scanIds),
    meta: { suppressGlobalError: true },
    onSuccess: (_result, scanIds) => {
      toast.show(scanIds.length === 1 ? 'Converted to PDF' : 'Scans merged into PDF', 'success');
      invalidateScans();
      setMergeSelection(new Set());
    },
    onError: () => toast.show('Failed to merge scans'),
  });

  // Close dropdown on outside click
  useEffect(() => {
    if (!openMenuId) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuId(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [openMenuId]);

  const applyEnhance = () => {
    if (!enhanceScanId || enhanceMutation.isPending) return;
    enhanceMutation.mutate({ scanId: enhanceScanId, form: enhanceForm });
  };

  const toggleMergeSelect = useCallback((scanId: string) => {
    setMergeSelection((prev) => {
      const next = new Set(prev);
      if (next.has(scanId)) next.delete(scanId);
      else next.add(scanId);
      return next;
    });
  }, []);

  const handleMerge = () => {
    if (mergeSelection.size < 1 || mergeMutation.isPending) return;
    const orderedIds = scans
      .filter((s) => mergeSelection.has(s.scan_id))
      .map((s) => s.scan_id);
    mergeMutation.mutate(orderedIds);
  };

  const handleToggleMenu = useCallback((scanId: string) => {
    setOpenMenuId((prev) => (prev === scanId ? null : scanId));
  }, []);

  // These are passed straight through to ScanRow as single-purpose callback
  // props: a `useState` setter or a mutation's `.mutate` are already stable
  // references on their own (TanStack Query memoizes `.mutate` per mutation
  // observer), so wrapping them in another `useCallback` here would add
  // nothing. Closing the actions dropdown after firing is composed inside
  // ScanRow itself (`runAndCloseMenu`), not baked in here.

  if (scansQuery.isPending) {
    return (
      <div className="flex flex-wrap gap-3">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} variant="thumbnail" />
        ))}
      </div>
    );
  }

  if (scansQuery.isError) {
    return <ErrorState onRetry={() => scansQuery.refetch()} />;
  }

  if (scans.length === 0) {
    return (
      <EmptyState
        icon={ScanLine}
        title="No scans yet"
        hint="Start a scan and it will appear here"
      />
    );
  }

  const completedScans = scans.filter((s) => s.status === 'completed');
  const mergeColumnVisible = completedScans.length >= 1;
  const mergeLabel = mergeSelection.size === 1 ? 'Convert to PDF' : `Merge ${mergeSelection.size} to PDF`;

  return (
    <>
    {/* Merge toolbar */}
    {completedScans.length >= 1 && (
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <span className="text-sm text-gray-600 dark:text-gray-400">
          {mergeSelection.size > 0
            ? `${mergeSelection.size} selected`
            : 'Select scans to merge or convert to PDF'}
        </span>
        {mergeSelection.size >= 1 && (
          <Button size="sm" onClick={handleMerge} disabled={mergeMutation.isPending}>
            {mergeMutation.isPending ? 'Processing...' : mergeLabel}
          </Button>
        )}
        {mergeSelection.size > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setMergeSelection(new Set())}>
            Clear
          </Button>
        )}
      </div>
    )}

    <div className="space-y-3">
      {scans.map((scan) => (
        <ScanRow
          key={scan.scan_id}
          scan={scan}
          merging={mergeSelection.has(scan.scan_id)}
          mergeColumnVisible={mergeColumnVisible}
          menuOpen={openMenuId === scan.scan_id}
          menuRef={openMenuId === scan.scan_id ? menuRef : undefined}
          onToggleMergeSelect={toggleMergeSelect}
          onPreview={setPreviewScan}
          onToggleMenu={handleToggleMenu}
          onEmail={setEmailScanId}
          onCloudSave={setCloudScanId}
          onPaperless={paperlessMutation.mutate}
          onOcr={ocrMutation.mutate}
          onEnhance={setEnhanceScanId}
          onConvert={convertMutation.mutate}
          onDelete={deleteMutation.mutate}
        />
      ))}
    </div>

    {emailScanId && (
      <EmailScanDialog scanId={emailScanId} onClose={() => setEmailScanId(null)} />
    )}
    {cloudScanId && (
      <CloudSaveDialog scanId={cloudScanId} onClose={() => setCloudScanId(null)} />
    )}
    {previewScan && (
      <FilePreviewModal
        url={getScanDownloadUrl(previewScan.scan_id)}
        thumbnailUrl={getScanThumbnailUrl(previewScan.scan_id)}
        filename={`scan_${previewScan.scan_id}.${previewScan.format}`}
        mimeType={scanMimeType(previewScan)}
        onClose={() => setPreviewScan(null)}
      />
    )}
    {enhanceScanId && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={closeEnhanceDialog} role="dialog" aria-label="Enhance scan">
        <div className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-6 shadow-md shadow-gray-200/50 dark:border-gray-800 dark:bg-gray-900 dark:shadow-none" onClick={(e) => e.stopPropagation()}>
          <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">Enhance Scan</h3>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Brightness ({enhanceForm.brightness.toFixed(1)})
              </label>
              <input type="range" min="0.1" max="3.0" step="0.1" value={enhanceForm.brightness}
                onChange={(e) => setEnhanceForm({ ...enhanceForm, brightness: parseFloat(e.target.value) })}
                aria-label="Brightness" className="w-full" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Contrast ({enhanceForm.contrast.toFixed(1)})
              </label>
              <input type="range" min="0.1" max="3.0" step="0.1" value={enhanceForm.contrast}
                onChange={(e) => setEnhanceForm({ ...enhanceForm, contrast: parseFloat(e.target.value) })}
                aria-label="Contrast" className="w-full" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Rotation</label>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Rotation angle">
                {[0, 90, 180, 270].map((deg) => (
                  <button key={deg} onClick={() => setEnhanceForm({ ...enhanceForm, rotation: deg })}
                    aria-pressed={enhanceForm.rotation === deg}
                    className={`px-3 py-1 text-sm rounded border ${enhanceForm.rotation === deg ? 'bg-ink-50 border-ink-300 text-ink-700 dark:bg-ink-950 dark:border-ink-700 dark:text-ink-300' : 'border-gray-300 text-gray-500 dark:border-gray-600 dark:text-gray-400'}`}
                  >{deg}&deg;</button>
                ))}
              </div>
            </div>
            <Toggle checked={enhanceForm.auto_crop} onChange={(v) => setEnhanceForm({ ...enhanceForm, auto_crop: v })} label="Auto-crop whitespace" />
            <Toggle checked={enhanceForm.deskew} onChange={(v) => setEnhanceForm({ ...enhanceForm, deskew: v })} label="Auto-deskew (straighten)" />
          </div>
          <div className="flex gap-2 justify-end mt-5">
            <Button size="sm" variant="secondary" onClick={closeEnhanceDialog}>Cancel</Button>
            <Button size="sm" onClick={applyEnhance} disabled={enhanceMutation.isPending}>
              {enhanceMutation.isPending ? 'Applying...' : 'Apply'}
            </Button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}
