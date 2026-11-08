import type { ScanJob } from '../types';

/** Only a finished feeder scan saved as a PDF, not already part of a merge,
 * can have its other side scanned. */
export function canScanBacks(
  scan: Pick<ScanJob, 'status' | 'source' | 'format' | 'duplex_merged'>,
): boolean {
  return (
    scan.status === 'completed' &&
    scan.source === 'ADF' &&
    scan.format === 'pdf' &&
    !scan.duplex_merged
  );
}
