import { useEffect } from 'react';
import { useJobStore } from '../../store/jobStore';
import StatusBadge from '../common/StatusBadge';
import Button from '../common/Button';

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(dateStr: string): string {
  return new Date(dateStr).toLocaleString();
}

const sourceLabels: Record<string, string> = {
  upload: 'Upload',
  smb: 'SMB',
  cloud: 'Cloud',
  email: 'Email',
  network: 'Network',
};

const sourceColors: Record<string, string> = {
  network: 'bg-blue-100 text-blue-700',
  email: 'bg-purple-100 text-purple-700',
  cloud: 'bg-cyan-100 text-cyan-700',
  smb: 'bg-orange-100 text-orange-700',
};

export default function JobQueue() {
  const { jobs, loading, fetchJobs, releaseJob, cancelJob, deleteJob } = useJobStore();

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  if (loading && jobs.length === 0) {
    return <p className="text-gray-500 text-sm">Loading jobs...</p>;
  }

  if (jobs.length === 0) {
    return <p className="text-gray-500 text-sm">No print jobs yet. Upload a file to get started.</p>;
  }

  return (
    <div className="space-y-3">
      {jobs.map((job) => (
        <div
          key={job.id}
          className="flex items-center justify-between p-4 bg-white rounded-lg border border-gray-200"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-900 truncate">{job.filename}</span>
              <StatusBadge status={job.status} />
              {job.source_type && job.source_type !== 'upload' && (
                <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${sourceColors[job.source_type] || 'bg-gray-100 text-gray-600'}`}>
                  {sourceLabels[job.source_type] || job.source_type}
                </span>
              )}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {formatSize(job.file_size)} &middot; {job.copies} cop{job.copies > 1 ? 'ies' : 'y'}
              {job.duplex && ' \u00b7 Duplex'}
              {' \u00b7 '}{job.media}
              {' \u00b7 '}{formatTime(job.created_at)}
            </div>
            {job.error_message && (
              <p className="text-xs text-red-600 mt-1">{job.error_message}</p>
            )}
          </div>

          <div className="flex gap-2 ml-4">
            {job.status === 'held' && (
              <Button size="sm" onClick={() => releaseJob(job.id)}>
                Print
              </Button>
            )}
            {['held', 'printing'].includes(job.status) && (
              <Button size="sm" variant="secondary" onClick={() => cancelJob(job.id)}>
                Cancel
              </Button>
            )}
            {['completed', 'failed', 'cancelled'].includes(job.status) && (
              <Button size="sm" variant="ghost" onClick={() => deleteJob(job.id)}>
                Delete
              </Button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
