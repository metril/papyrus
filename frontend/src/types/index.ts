// --- Auth ---

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: string;
}

export interface APIToken {
  id: string;
  name: string;
  permissions: string[];
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface APITokenCreated extends APIToken {
  token: string;
}

// --- Print Jobs ---

export interface PrintJob {
  id: number;
  cups_job_id: number | null;
  title: string;
  filename: string;
  file_size: number;
  mime_type: string;
  status: 'held' | 'converting' | 'printing' | 'completed' | 'failed' | 'cancelled';
  copies: number;
  duplex: boolean;
  media: string;
  source_type: 'upload' | 'smb';
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface PrintJobUpload {
  copies: number;
  duplex: boolean;
  media: string;
  hold: boolean;
}

// --- Scanner ---

export interface ScanJob {
  id: number;
  scan_id: string;
  status: 'scanning' | 'completed' | 'failed' | 'deleted';
  resolution: number;
  mode: 'Color' | 'Gray' | 'Lineart';
  format: 'png' | 'jpeg' | 'tiff' | 'pdf';
  source: 'Flatbed' | 'ADF';
  page_count: number;
  file_size: number | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface ScanRequest {
  resolution: number;
  mode: string;
  format: string;
  source: string;
}

// --- Copy ---

export interface CopyRequest {
  resolution: number;
  mode: string;
  source: string;
  copies: number;
  duplex: boolean;
  media: string;
}

// --- SMB ---

export interface SMBShare {
  id: number;
  name: string;
  server: string;
  share_name: string;
  username: string | null;
  domain: string;
  created_at: string;
}

export interface SMBFileEntry {
  name: string;
  is_directory: boolean;
  size: number;
  modified_at: string | null;
}

// --- Printer ---

export interface PrinterStatus {
  state: number;
  state_message: string;
  accepting_jobs: boolean;
}

// --- WebSocket ---

export interface WSMessage {
  type: string;
  data: Record<string, unknown>;
}
