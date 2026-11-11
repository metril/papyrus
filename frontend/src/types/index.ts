import type { components } from './api.gen';

type S = components['schemas'];

// Server shapes: aliases of the generated OpenAPI types (npm run gen:api).
export type User = S['UserResponse'];
export type APIToken = S['APITokenResponse'];
export type APITokenCreated = S['APITokenCreated'];
export type PrintJob = S['PrintJobResponse'];
export type ScanJob = S['ScanResponse'];
export type ScanRequest = S['ScanRequest'];
export type CopyRequest = S['CopyRequest'];
export type SMBShare = S['SMBShareResponse'];
export type SMBFileEntry = S['SMBFileEntry'];
// The server types `provider` as a plain string; the client switches on the known set.
export type CloudProvider = Omit<S['CloudProviderEntry'], 'provider'> & {
  provider: 'gdrive' | 'dropbox' | 'onedrive' | 'webdav';
};
export type CloudFileEntry = S['CloudFileEntry'];
export type PrinterStatus = S['PrinterStatus'];
export type ManagedPrinter = S['PrinterInfo'];
export type ManagedPrinterCreate = S['PrinterCreate'];
export type ManagedPrinterUpdate = S['PrinterUpdate'];
export type DiscoveredPrinter = S['DiscoveredPrinter'];
export type ManagedScanner = S['ScannerResponse'];
export type ManagedScannerCreate = S['ScannerCreate'];
export type ManagedScannerUpdate = S['ScannerUpdate'];
export type DiscoveredDevice = S['DiscoveredScanner'];

// Client-only types.

export interface PrintJobUpload {
  copies: number;
  duplex: boolean;
  media: string;
  hold: boolean;
}

export interface WSMessage {
  type: string;
  data: Record<string, unknown>;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type Contact = S['ContactResponse'];
export type KioskResponse = S['KioskResponse'];
export type KioskTokenResponse = S['KioskTokenResponse'];
