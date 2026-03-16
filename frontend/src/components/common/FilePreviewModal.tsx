import { useEffect, useState, useCallback, useRef } from 'react';
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

function Spinner() {
  return (
    <svg className="animate-spin h-8 w-8 text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

export default function FilePreviewModal({ url, previewUrl, filename, mimeType, onClose }: FilePreviewModalProps) {
  const isPdf = mimeType === 'application/pdf';
  const isImage = mimeType.startsWith('image/') && !mimeType.includes('tiff');
  const isOffice = OFFICE_MIMES.has(mimeType);
  const needsIframe = isPdf || isOffice;
  const canPreview = isPdf || isImage || (isOffice && !!previewUrl);

  const viewUrl = isOffice && previewUrl ? previewUrl : url;

  const [fetchState, setFetchState] = useState<'loading' | 'ready' | 'error'>(needsIframe ? 'loading' : 'ready');
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const controllerRef = useRef<AbortController | null>(null);

  const fetchPreview = useCallback(() => {
    if (!needsIframe || !canPreview) return;

    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setFetchState('loading');
    setErrorMsg('');

    fetch(viewUrl, { signal: controller.signal, credentials: 'include' })
      .then((res) => {
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        const objUrl = URL.createObjectURL(blob);
        setBlobUrl(objUrl);
        setFetchState('ready');
      })
      .catch((err) => {
        if (err.name === 'AbortError') return;
        setErrorMsg(err.message || 'Preview failed');
        setFetchState('error');
      });
  }, [viewUrl, needsIframe, canPreview]);

  useEffect(() => {
    fetchPreview();
    return () => {
      controllerRef.current?.abort();
    };
  }, [fetchPreview]);

  // Clean up blob URL on unmount
  useEffect(() => {
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [blobUrl]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [onClose]);

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
          {needsIframe && canPreview && fetchState === 'loading' && (
            <div className="flex flex-col items-center gap-3">
              <Spinner />
              <p className="text-sm text-gray-500 dark:text-gray-400">
                {isOffice ? 'Converting document...' : 'Loading preview...'}
              </p>
            </div>
          )}
          {needsIframe && canPreview && fetchState === 'ready' && blobUrl && (
            <iframe src={blobUrl} className="w-full h-full rounded border border-gray-200 dark:border-gray-700" title={filename} />
          )}
          {needsIframe && canPreview && fetchState === 'error' && (
            <div className="text-center">
              <p className="text-red-600 dark:text-red-400 mb-1">Preview failed</p>
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">{errorMsg}</p>
              <Button size="sm" onClick={fetchPreview}>Retry</Button>
            </div>
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
