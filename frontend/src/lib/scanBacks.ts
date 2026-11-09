import type { ScanJob } from '../types';

/** Only a finished feeder scan saved as a PDF, not already part of a merge and not a back-side scan,
 * can have its other side scanned. */
export function canScanBacks(
  scan: Pick<ScanJob, 'status' | 'source' | 'format' | 'duplex_merged' | 'duplex_source_scan_id'>,
): boolean {
  return (
    scan.status === 'completed' &&
    scan.source === 'ADF' &&
    scan.format === 'pdf' &&
    !scan.duplex_merged &&
    !scan.duplex_source_scan_id
  );
}
