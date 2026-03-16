import { useEffect } from 'react';
import Button from './Button';

const OFFICE_MIMES = new Set([
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.oasis.opendocument.text',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.oasis.opendocument.spreadsheet',
  'application/vnd.ms-powerpoint',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/vnd.oasis.opendocument.presentation',
]);

interface FilePreviewModalProps {
  url: string;
  previewUrl?: string;
  filename: string;
  mimeType: string;
  onClose: () => void;
}

export default function FilePreviewModal({ url, previewUrl, filename, mimeType, onClose }: FilePreviewModalProps) {
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  const isPdf = mimeType === 'application/pdf';
  const isImage = mimeType.startsWith('image/') && !mimeType.includes('tiff');
  const isOffice = OFFICE_MIMES.has(mimeType);
  const canPreview = isPdf || isImage || (isOffice && !!previewUrl);

  // Office docs use the preview endpoint (serves converted PDF)
  const viewUrl = isOffice && previewUrl ? previewUrl : url;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-4xl mx-4 flex flex-col"
        style={{ height: '80vh' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">{filename}</h3>
          <div className="flex items-center gap-2">
            <a href={url} download>
              <Button size="sm" variant="secondary">Download</Button>
            </a>
            <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-4 flex items-center justify-center bg-gray-50 dark:bg-gray-950">
          {(isPdf || isOffice) && canPreview && (
            <iframe src={viewUrl} className="w-full h-full rounded border border-gray-200 dark:border-gray-700" title={filename} />
          )}
          {isImage && (
            <img src={viewUrl} alt={filename} className="max-w-full max-h-full object-contain" />
          )}
          {!canPreview && (
            <div className="text-center">
              <p className="text-gray-600 dark:text-gray-400 mb-3">Preview not available for this file type.</p>
              <a href={url} download>
                <Button>Download {filename}</Button>
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
