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
  source_type: 'upload' | 'smb' | 'cloud' | 'email' | 'network';
  printer_id: number | null;
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

// --- Cloud ---

export interface CloudProvider {
  id: number;
  provider: 'gdrive' | 'dropbox' | 'onedrive';
  connected_at: string;
}

export interface CloudFileEntry {
  name: string;
  id: string;
  is_directory: boolean;
  size: number | null;
  modified_at: string | null;
  mime_type: string | null;
}

// --- Printer ---

export interface PrinterStatus {
  state: number;
  state_message: string;
  accepting_jobs: boolean;
}

export interface ManagedPrinter {
  id: number;
  display_name: string;
  cups_name: string;
  uri: string;
  description: string | null;
  is_default: boolean;
  is_network_queue: boolean;
  auto_release: boolean;
  created_at: string;
  cups_status: PrinterStatus;
}

export interface ManagedPrinterCreate {
  display_name: string;
  uri?: string;
  description?: string;
  is_network_queue?: boolean;
  auto_release?: boolean;
}

export interface ManagedPrinterUpdate {
  display_name?: string;
  uri?: string;
  description?: string;
  auto_release?: boolean;
}

// --- Scanner Management ---

export interface ManagedScanner {
  id: number;
  name: string;
  device: string;
  description: string | null;
  is_default: boolean;
  auto_deliver: boolean;
  post_scan_config: Record<string, unknown> | null;
  created_at: string;
}

export interface ManagedScannerCreate {
  name: string;
  device: string;
  description?: string;
  auto_deliver?: boolean;
  post_scan_config?: Record<string, unknown>;
}

export interface ManagedScannerUpdate {
  name?: string;
  device?: string;
  description?: string;
  auto_deliver?: boolean;
  post_scan_config?: Record<string, unknown>;
}

export interface DiscoveredDevice {
  device: string;
  description: string;
}

// --- WebSocket ---

export interface WSMessage {
  type: string;
  data: Record<string, unknown>;
}
