import type { ScanJob } from '../types';

/** Only a finished feeder scan saved as a PDF can have its other side scanned. */
export function canScanBacks(scan: Pick<ScanJob, 'status' | 'source' | 'format'>): boolean {
  return scan.status === 'completed' && scan.source === 'ADF' && scan.format === 'pdf';
}
