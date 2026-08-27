import type { CloudProvider } from '../types';

/**
 * Human-readable label per connected cloud-storage provider. Shared by
 * FilesPage, CloudStorageCard and CloudSaveDialog so all three surfaces stay
 * in sync — previously each kept its own partial copy of this map (F149),
 * and 'webdav' was missing from at least one of them (F82).
 */
export const providerLabels: Record<CloudProvider['provider'], string> = {
  gdrive: 'Google Drive',
  dropbox: 'Dropbox',
  onedrive: 'OneDrive',
  webdav: 'WebDAV / Nextcloud',
};

/** Falls back to the raw provider string for a value TS hasn't seen yet,
 * rather than rendering nothing. */
export function getProviderLabel(provider: string): string {
  return (providerLabels as Record<string, string>)[provider] ?? provider;
}
