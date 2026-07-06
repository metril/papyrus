import { memo, useState } from 'react';
import type { RefObject } from 'react';
import {
  ChevronDown,
  Cloud,
  Download,
  Eye,
  FileOutput,
  FileSearch,
  FolderUp,
  ImageOff,
  Mail,
  Trash2,
  Wand2,
} from 'lucide-react';
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

// Paper-framed thumbnail: a bordered, lightly-shadowed mount around the
// preview image (or a muted fallback glyph if the image 404s/errors), never
// a bare <img> with its own border.
function ScanThumbnailComponent({ scanId }: ScanThumbnailProps) {
  const [failed, setFailed] = useState(false);

  return (
    <div className="h-12 w-12 shrink-0 overflow-hidden rounded border border-gray-200 bg-gray-50 shadow-sm dark:border-gray-700 dark:bg-gray-800">
      {failed ? (
        <div className="flex h-full w-full items-center justify-center">
          <ImageOff className="h-4 w-4 text-gray-300 dark:text-gray-600" strokeWidth={1.75} aria-hidden="true" />
        </div>
      ) : (
        <img
          src={getScanThumbnailUrl(scanId)}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      )}
    </div>
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
      className={`flex flex-col gap-3 rounded-lg border bg-white p-4 dark:bg-gray-900 sm:flex-row sm:items-center sm:justify-between ${merging ? 'border-ink-400 ring-1 ring-ink-200 dark:border-ink-500 dark:ring-ink-800' : 'border-gray-200 dark:border-gray-700'}`}
    >
      <div className="flex min-w-0 flex-1 items-center gap-3">
        {/* Merge checkbox */}
        {scan.status === 'completed' && mergeColumnVisible && (
          <input
            type="checkbox"
            checked={merging}
            onChange={() => onToggleMergeSelect(scan.scan_id)}
            className="shrink-0"
          />
        )}

        {scan.status === 'completed' && <ScanThumbnail scanId={scan.scan_id} />}

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => scan.status === 'completed' && onPreview(scan)}
              className={`truncate text-left text-sm font-medium ${scan.status === 'completed' ? 'text-ink-600 hover:underline cursor-pointer dark:text-ink-400' : 'text-gray-900 dark:text-gray-100'}`}
            >
              {scan.format.toUpperCase()} &middot; {scan.resolution} DPI &middot; {scan.mode}
            </button>
            <StatusBadge status={scan.status} />
          </div>
          <div className="mt-1 font-mono text-xs text-gray-500 dark:text-gray-400">
            {scan.source}
            {scan.page_count > 1 && ` · ${scan.page_count} pages`}
            {scan.file_size && ` · ${formatSize(scan.file_size)}`}
            {' · '}{new Date(scan.created_at).toLocaleString()}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 sm:ml-4 sm:shrink-0">
        {scan.status === 'completed' && (
          <>
            <Button size="sm" variant="secondary" onClick={() => onPreview(scan)}>
              <Eye className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
              View
            </Button>
            <a href={getScanDownloadUrl(scan.scan_id)} download>
              <Button size="sm" variant="secondary">
                <Download className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
                Download
              </Button>
            </a>
            {/* Actions dropdown */}
            <div className="relative" ref={menuOpen ? menuRef : undefined}>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => onToggleMenu(scan.scan_id)}
              >
                Actions
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform duration-200 ${menuOpen ? 'rotate-180' : ''}`}
                  strokeWidth={1.75}
                  aria-hidden="true"
                />
              </Button>
              {menuOpen && (
                <div className="absolute right-0 top-full z-20 mt-1 w-52 rounded-lg border border-gray-200 bg-white py-1 shadow-md shadow-gray-200/50 dark:border-gray-700 dark:bg-gray-800 dark:shadow-none">
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
                    onClick={() => runAndCloseMenu(onEmail)}
                  >
                    <Mail className="h-4 w-4 text-gray-400" strokeWidth={1.75} aria-hidden="true" />
                    Email
                  </button>
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
                    onClick={() => runAndCloseMenu(onCloudSave)}
                  >
                    <Cloud className="h-4 w-4 text-gray-400" strokeWidth={1.75} aria-hidden="true" />
                    Save to Cloud
                  </button>
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
                    onClick={() => runAndCloseMenu(onPaperless)}
                  >
                    <FolderUp className="h-4 w-4 text-gray-400" strokeWidth={1.75} aria-hidden="true" />
                    Send to Paperless
                  </button>
                  {scan.format === 'pdf' && (
                    <button
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
                      onClick={() => runAndCloseMenu(onOcr)}
                    >
                      <FileSearch className="h-4 w-4 text-gray-400" strokeWidth={1.75} aria-hidden="true" />
                      OCR
                    </button>
                  )}
                  {scan.format !== 'pdf' && (
                    <>
                      <button
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
                        onClick={() => runAndCloseMenu(onEnhance)}
                      >
                        <Wand2 className="h-4 w-4 text-gray-400" strokeWidth={1.75} aria-hidden="true" />
                        Enhance
                      </button>
                      <button
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
                        onClick={() => runAndCloseMenu(onConvert)}
                      >
                        <FileOutput className="h-4 w-4 text-gray-400" strokeWidth={1.75} aria-hidden="true" />
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
          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden="true" />
          Delete
        </Button>
      </div>
    </div>
  );
}

const ScanRow = memo(ScanRowComponent);
export default ScanRow;
