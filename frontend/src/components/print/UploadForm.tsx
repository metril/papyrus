import { useState, useCallback } from 'react';
import { useDropzone } from 'react-dropzone';
import Button from '../common/Button';
import { uploadPrintJob } from '../../api/printer';
import { useJobStore } from '../../store/jobStore';

export default function UploadForm() {
  const [files, setFiles] = useState<File[]>([]);
  const [copies, setCopies] = useState(1);
  const [duplex, setDuplex] = useState(false);
  const [media, setMedia] = useState('A4');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetchJobs = useJobStore((s) => s.fetchJobs);

  const onDrop = useCallback((acceptedFiles: File[]) => {
    setFiles(acceptedFiles);
    setError(null);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/pdf': ['.pdf'],
      'image/*': ['.jpg', '.jpeg', '.png', '.tiff', '.tif'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'application/vnd.oasis.opendocument.text': ['.odt'],
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
      'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx'],
    },
    maxFiles: 10,
  });

  const handleUpload = async () => {
    if (files.length === 0) return;
    setUploading(true);
    setError(null);

    try {
      for (const file of files) {
        await uploadPrintJob(file, { copies, duplex, media, hold: true });
      }
      setFiles([]);
      await fetchJobs();
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
        className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors
          ${isDragActive ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-gray-400'}`}
      >
        <input {...getInputProps()} />
        {isDragActive ? (
          <p className="text-blue-600 font-medium">Drop files here...</p>
        ) : (
          <div>
            <p className="text-gray-600">Drag & drop files here, or click to browse</p>
            <p className="text-sm text-gray-400 mt-1">PDF, images, DOCX, ODT, XLSX, PPTX</p>
          </div>
        )}
      </div>

      {files.length > 0 && (
        <div className="space-y-3">
          <div className="text-sm text-gray-600">
            {files.length} file{files.length > 1 ? 's' : ''} selected:
          </div>
          <ul className="text-sm space-y-1">
            {files.map((f) => (
              <li key={f.name} className="text-gray-700">
                {f.name} ({(f.size / 1024).toFixed(1)} KB)
              </li>
            ))}
          </ul>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Copies</label>
              <input
                type="number"
                min={1}
                max={99}
                value={copies}
                onChange={(e) => setCopies(Number(e.target.value))}
                className="w-full rounded-lg border-gray-300 shadow-sm text-sm p-2 border"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Paper</label>
              <select
                value={media}
                onChange={(e) => setMedia(e.target.value)}
                className="w-full rounded-lg border-gray-300 shadow-sm text-sm p-2 border"
              >
                <option value="A4">A4</option>
                <option value="Letter">Letter</option>
                <option value="Legal">Legal</option>
              </select>
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={duplex}
                  onChange={(e) => setDuplex(e.target.checked)}
                  className="rounded border-gray-300"
                />
                Duplex
              </label>
            </div>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <Button onClick={handleUpload} disabled={uploading} className="w-full">
            {uploading ? 'Uploading...' : `Upload & Hold ${files.length} file${files.length > 1 ? 's' : ''}`}
          </Button>
        </div>
      )}
    </div>
  );
}
