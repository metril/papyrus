import { memo, useState } from 'react';
import type { RefObject } from 'react';
import { getScanDownloadUrl, getScanThumbnailUrl } from '../../api/scanner';
import StatusBadge from '../common/StatusBadge';
import Button from '../common/Button';
import type { ScanJob } from '../../types';

function formatSize(bytes: number | null): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface ScanThumbnailProps {
  scanId: string;
}

function ScanThumbnailComponent({ scanId }: ScanThumbnailProps) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <div className="w-12 h-12 rounded border border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800 mr-3 shrink-0" />
    );
  }

  return (
    <img
      src={getScanThumbnailUrl(scanId)}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className="w-12 h-12 object-cover rounded border border-gray-200 dark:border-gray-700 mr-3 shrink-0 bg-gray-100 dark:bg-gray-800"
    />
  );
}

// Pure props (just an id) — memoize so a sibling row's re-render never
// re-triggers this row's own thumbnail <img>.
export const ScanThumbnail = memo(ScanThumbnailComponent);

export interface ScanRowProps {
  scan: ScanJob;
  /** Primitive: whether this scan is in the current merge-selection set. */
  merging: boolean;
  /** Whether the merge-selection checkbox column is shown at all right now. */
  mergeColumnVisible: boolean;
  /** Whether THIS row's actions dropdown is open. */
  menuOpen: boolean;
  /** Outside-click-detection ref, only ever passed to the currently-open row. */
  menuRef?: RefObject<HTMLDivElement | null>;
  onToggleMergeSelect: (scanId: string) => void;
  onPreview: (scan: ScanJob) => void;
  onToggleMenu: (scanId: string) => void;
  onEmail: (scanId: string) => void;
  onCloudSave: (scanId: string) => void;
  onPaperless: (scanId: string) => void;
  onOcr: (scanId: string) => void;
  onEnhance: (scanId: string) => void;
  onConvert: (scanId: string) => void;
  onDelete: (scanId: string) => void;
}

/**
 * A single scan row. Exported separately (un-memoized) as `ScanRowComponent`
 * so tests can wrap it themselves; the default export is what production
 * code renders.
 */
export function ScanRowComponent({
  scan,
  merging,
  mergeColumnVisible,
  menuOpen,
  menuRef,
  onToggleMergeSelect,
  onPreview,
  onToggleMenu,
  onEmail,
  onCloudSave,
  onPaperless,
  onOcr,
  onEnhance,
  onConvert,
  onDelete,
}: ScanRowProps) {
  // Every dropdown action both fires its own effect and closes the menu;
  // `onToggleMenu` flips `openMenuId` for this scan id, which — since the menu
  // is only ever open for one scan at a time — always resolves to "closed"
  // here. Composed at the call site (rather than baked into each callback
  // prop) so the individual action callbacks stay single-purpose refs
  // (`mutation.mutate` / a raw `useState` setter) with no wrapping required.
  const runAndCloseMenu = (fn: (scanId: string) => void) => {
    fn(scan.scan_id);
    onToggleMenu(scan.scan_id);
  };

  return (
    <div
      className={`flex items-center justify-between p-4 bg-white dark:bg-gray-900 rounded-lg border ${merging ? 'border-blue-400 dark:border-blue-500 ring-1 ring-blue-200 dark:ring-blue-800' : 'border-gray-200 dark:border-gray-700'}`}
    >
      {/* Merge checkbox */}
      {scan.status === 'completed' && mergeColumnVisible && (
        <input
          type="checkbox"
          checked={merging}
          onChange={() => onToggleMergeSelect(scan.scan_id)}
          className="mr-3 rounded border-gray-300 dark:border-gray-600"
        />
      )}

      {scan.status === 'completed' && <ScanThumbnail scanId={scan.scan_id} />}

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <button
            onClick={() => scan.status === 'completed' && onPreview(scan)}
            className={`text-sm font-medium truncate text-left ${scan.status === 'completed' ? 'text-blue-600 dark:text-blue-400 hover:underline cursor-pointer' : 'text-gray-900 dark:text-gray-100'}`}
          >
            {scan.format.toUpperCase()} &middot; {scan.resolution} DPI &middot; {scan.mode}
          </button>
          <StatusBadge status={scan.status} />
        </div>
        <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          {scan.source}
          {scan.page_count > 1 && ` · ${scan.page_count} pages`}
          {scan.file_size && ` · ${formatSize(scan.file_size)}`}
          {' · '}{new Date(scan.created_at).toLocaleString()}
        </div>
      </div>

      <div className="flex gap-2 ml-4 items-center">
        {scan.status === 'completed' && (
          <>
            <Button size="sm" variant="secondary" onClick={() => onPreview(scan)}>
              View
            </Button>
            <a href={getScanDownloadUrl(scan.scan_id)} download>
              <Button size="sm" variant="secondary">Download</Button>
            </a>
            {/* Actions dropdown */}
            <div className="relative" ref={menuOpen ? menuRef : undefined}>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => onToggleMenu(scan.scan_id)}
              >
                Actions &#9662;
              </Button>
              {menuOpen && (
                <div className="absolute right-0 top-full mt-1 w-44 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-20 py-1">
                  <button
                    className="w-full text-left px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
                    onClick={() => runAndCloseMenu(onEmail)}
                  >
                    Email
                  </button>
                  <button
                    className="w-full text-left px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
                    onClick={() => runAndCloseMenu(onCloudSave)}
                  >
                    Save to Cloud
                  </button>
                  <button
                    className="w-full text-left px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
                    onClick={() => runAndCloseMenu(onPaperless)}
                  >
                    Send to Paperless
                  </button>
                  {scan.format === 'pdf' && (
                    <button
                      className="w-full text-left px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
                      onClick={() => runAndCloseMenu(onOcr)}
                    >
                      OCR
                    </button>
                  )}
                  {scan.format !== 'pdf' && (
                    <>
                      <button
                        className="w-full text-left px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
                        onClick={() => runAndCloseMenu(onEnhance)}
                      >
                        Enhance
                      </button>
                      <button
                        className="w-full text-left px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
                        onClick={() => runAndCloseMenu(onConvert)}
                      >
                        Convert to PDF
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          </>
        )}
        <Button size="sm" variant="danger" onClick={() => onDelete(scan.scan_id)}>
          Delete
        </Button>
      </div>
    </div>
  );
}

const ScanRow = memo(ScanRowComponent);
export default ScanRow;
