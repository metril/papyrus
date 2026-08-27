import { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useDropzone, type FileRejection } from 'react-dropzone';
import { Upload } from 'lucide-react';
import Button from '../common/Button';
import Toggle from '../common/Toggle';
import { uploadPrintJob } from '../../api/printer';
import { applyJobEvent } from '../../hooks/useRealtimeBridge';
import { useToast } from '../../hooks/useToast';
import type { PrintJob } from '../../types';

const ACCEPTED_TYPES = {
  'application/pdf': ['.pdf'],
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/png': ['.png'],
  'image/tiff': ['.tiff', '.tif'],
  'application/msword': ['.doc'],
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
  'application/vnd.oasis.opendocument.text': ['.odt'],
  'application/vnd.ms-excel': ['.xls'],
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
  'application/vnd.oasis.opendocument.spreadsheet': ['.ods'],
  'application/vnd.ms-powerpoint': ['.ppt'],
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx'],
  'application/vnd.oasis.opendocument.presentation': ['.odp'],
};

const MIN_COPIES = 1;
const MAX_COPIES = 99;

// F151: same-named files from different folders (or a multi-folder picker
// selection) used to collapse into one queue entry because dedup/removal
// only ever looked at `f.name`. size+lastModified distinguish files that
// merely share a name.
function fileKey(f: File): string {
  return `${f.name}:${f.size}:${f.lastModified}`;
}

/** Clamps a copies input string to the 1–99 range the backend accepts,
 * treating anything unparseable (or an empty field) as 1 rather than 0
 * (F89) — `Number('')` is 0, which used to be sent to the server verbatim. */
function clampCopies(raw: string): number {
  if (raw.trim() === '') return MIN_COPIES;
  const n = Number(raw);
  if (!Number.isFinite(n)) return MIN_COPIES;
  return Math.min(MAX_COPIES, Math.max(MIN_COPIES, Math.trunc(n)));
}

export default function UploadForm() {
  const [files, setFiles] = useState<File[]>([]);
  // Kept as the raw typed string (not the clamped number) so a field the
  // user has cleared can stay visually empty while they type a new
  // multi-digit value, rather than snapping back to "1" after every
  // keystroke. `copies` below is always the clamped, submit-ready value.
  const [copiesInput, setCopiesInput] = useState('1');
  const [duplex, setDuplex] = useState(false);
  const [media, setMedia] = useState('A4');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const toast = useToast();

  const copies = clampCopies(copiesInput);

  // A fresh upload is a new job: prepend it into the cache (grow total). The
  // WS `job_created` broadcast that follows is an idempotent same-id replace.
  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadPrintJob(file, { copies, duplex, media, hold: true }),
    meta: { suppressGlobalError: true },
    onSuccess: (job: PrintJob) =>
      applyJobEvent(queryClient, {
        type: 'job_created',
        data: job as unknown as Record<string, unknown>,
      }),
  });

  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      const existingKeys = new Set(files.map(fileKey));
      const toAdd: File[] = [];
      let duplicateCount = 0;
      for (const f of acceptedFiles) {
        const key = fileKey(f);
        if (existingKeys.has(key)) {
          duplicateCount += 1;
          continue;
        }
        existingKeys.add(key);
        toAdd.push(f);
      }
      if (toAdd.length > 0) {
        setFiles((prev) => [...prev, ...toAdd]);
      }
      if (duplicateCount > 0) {
        toast.show(
          duplicateCount === 1
            ? 'Skipped 1 file already in the queue'
            : `Skipped ${duplicateCount} files already in the queue`,
        );
      }
      setError(null);
    },
    [files, toast],
  );

  const onDropRejected = useCallback((rejections: FileRejection[]) => {
    const names = rejections.map((r) => r.file.name);
    setError(`Unsupported file${names.length > 1 ? 's' : ''}: ${names.join(', ')}`);
  }, []);

  const removeFile = (key: string) => {
    setFiles((prev) => prev.filter((f) => fileKey(f) !== key));
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    onDropRejected,
    accept: ACCEPTED_TYPES,
    maxFiles: 10,
  });

  const handleUpload = async () => {
    if (files.length === 0) return;
    setUploading(true);
    setError(null);

    try {
      for (const file of files) {
        await uploadMutation.mutateAsync(file);
        // F86: drop each file the moment it succeeds rather than only
        // clearing the whole list after every file in the batch resolves —
        // a later file failing (413, unsupported type, ...) used to leave
        // already-uploaded files sitting in the visible queue, inviting a
        // second, duplicate upload on retry.
        const key = fileKey(file);
        setFiles((prev) => prev.filter((f) => fileKey(f) !== key));
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Upload failed';
      setError(message);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div
        {...getRootProps()}
        className={`rounded-xl border-2 border-dashed p-8 text-center cursor-pointer transition-colors
          ${isDragActive
            ? 'border-ink-500 bg-ink-50 dark:bg-ink-950/50'
            : 'border-gray-300 hover:border-gray-400 dark:border-gray-700 dark:hover:border-gray-500'}`}
      >
        <input {...getInputProps()} />
        <Upload
          className={`mx-auto mb-3 h-8 w-8 ${isDragActive ? 'text-ink-500 dark:text-ink-400' : 'text-gray-400 dark:text-gray-500'}`}
          strokeWidth={1.75}
          aria-hidden="true"
        />
        {isDragActive ? (
          <p className="font-medium text-ink-600 dark:text-ink-400">Drop files here...</p>
        ) : (
          <div>
            <p className="text-gray-600 dark:text-gray-400">Drag & drop files here, or click to browse</p>
            <p className="mt-1 font-mono text-xs text-gray-400 dark:text-gray-500">PDF, images, DOC(X), ODT, XLS(X), ODS, PPT(X), ODP</p>
          </div>
        )}
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {files.length > 0 && (
        <div className="space-y-3">
          <div className="text-sm text-gray-600 dark:text-gray-400">
            {files.length} file{files.length > 1 ? 's' : ''} selected:
          </div>
          <ul className="text-sm space-y-1">
            {files.map((f) => (
              <li key={fileKey(f)} className="flex items-center justify-between text-gray-700 dark:text-gray-300">
                <span className="truncate">
                  {f.name} <span className="font-mono text-gray-500 dark:text-gray-400">({(f.size / 1024).toFixed(1)} KB)</span>
                </span>
                <button
                  type="button"
                  onClick={() => removeFile(fileKey(f))}
                  className="ml-2 text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-950/50 rounded-full w-6 h-6 flex items-center justify-center shrink-0 text-base font-bold"
                  title="Remove file"
                >
                  &times;
                </button>
              </li>
            ))}
          </ul>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Copies</label>
              <input
                type="number"
                min={MIN_COPIES}
                max={MAX_COPIES}
                value={copiesInput}
                onChange={(e) => {
                  const raw = e.target.value;
                  setCopiesInput(raw === '' ? '' : String(clampCopies(raw)));
                }}
                onBlur={() => {
                  if (copiesInput === '') setCopiesInput('1');
                }}
                className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Paper</label>
              <select
                value={media}
                onChange={(e) => setMedia(e.target.value)}
                className="w-full rounded-lg border-gray-300 dark:border-gray-600 shadow-sm text-sm p-2 border bg-white dark:bg-gray-800 dark:text-gray-100"
              >
                <option value="A4">A4</option>
                <option value="Letter">Letter</option>
                <option value="Legal">Legal</option>
              </select>
            </div>
            <div className="flex items-end">
              <Toggle checked={duplex} onChange={setDuplex} label="Duplex" />
            </div>
          </div>

          <Button onClick={handleUpload} disabled={uploading} className="w-full">
            {uploading ? 'Uploading...' : `Upload & Hold ${files.length} file${files.length > 1 ? 's' : ''}`}
          </Button>
        </div>
      )}
    </div>
  );
}
