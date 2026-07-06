import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HistoryRowComponent } from './HistoryRow';
import type { HistoryRowProps } from './HistoryRow';
import type { HistoryItem } from '../../pages/HistoryPage';
import type { PrintJob, ScanJob } from '../../types';

const rawJob: PrintJob = {
  id: 1,
  cups_job_id: null,
  title: 'Doc',
  filename: 'doc.pdf',
  file_size: 1024,
  mime_type: 'application/pdf',
  status: 'completed',
  copies: 1,
  duplex: false,
  media: 'A4',
  source_type: 'upload',
  printer_id: null,
  has_pin: false,
  error_message: null,
  created_at: '2026-07-05T00:00:00Z',
  updated_at: '2026-07-05T00:00:00Z',
  completed_at: '2026-07-05T00:05:00Z',
};

const rawScan: ScanJob = {
  id: 7,
  scan_id: 'scan-abc',
  status: 'completed',
  resolution: 300,
  mode: 'Color',
  format: 'pdf',
  source: 'Flatbed',
  page_count: 1,
  file_size: 2048,
  error_message: null,
  created_at: '2026-07-04T00:00:00Z',
  completed_at: '2026-07-04T00:00:00Z',
};

const printItem: HistoryItem = {
  type: 'print',
  id: 'print-1',
  numericId: 1,
  label: 'doc.pdf',
  status: 'completed',
  time: '2026-07-05T00:00:00Z',
  detail: '1 copy · A4',
  downloadUrl: '/api/jobs/1/download',
  previewUrl: '/api/jobs/1/preview',
  mimeType: 'application/pdf',
  filename: 'doc.pdf',
  raw: rawJob,
};

const scanItem: HistoryItem = {
  type: 'scan',
  id: 'scan-scan-abc',
  numericId: 7,
  scanId: 'scan-abc',
  label: 'PDF 300 DPI',
  status: 'completed',
  time: '2026-07-04T00:00:00Z',
  detail: 'Color · Flatbed',
  downloadUrl: '/api/scanner/scans/scan-abc/download',
  mimeType: 'application/pdf',
  filename: 'scan_scan-abc.pdf',
  raw: rawScan,
};

function noop() {}

function makeProps(overrides: Partial<HistoryRowProps> = {}): HistoryRowProps {
  return {
    item: printItem,
    selected: false,
    onToggleSelect: noop,
    onPreview: noop,
    onDeleteJob: noop,
    onDeleteScan: noop,
    ...overrides,
  };
}

describe('HistoryRow thumbnail', () => {
  it('renders a job thumbnail via the job-thumbnail endpoint for print items', () => {
    const { container } = render(<HistoryRowComponent {...makeProps({ item: printItem })} />);
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute('src', '/api/jobs/1/thumbnail');
    expect(img).toHaveAttribute('loading', 'lazy');
  });

  it('renders a scan thumbnail via the scan-thumbnail endpoint for scan items', () => {
    const { container } = render(<HistoryRowComponent {...makeProps({ item: scanItem })} />);
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute('src', '/api/scanner/scans/scan-abc/thumbnail');
  });

  it('falls back to the Printer glyph on image error for a print item', () => {
    const { container } = render(<HistoryRowComponent {...makeProps({ item: printItem })} />);
    const img = container.querySelector('img')!;

    fireEvent.error(img);

    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('svg.lucide-printer')).not.toBeNull();
  });

  it('falls back to the ScanLine glyph on image error for a scan item', () => {
    const { container } = render(<HistoryRowComponent {...makeProps({ item: scanItem })} />);
    const img = container.querySelector('img')!;

    fireEvent.error(img);

    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('svg.lucide-scan-line')).not.toBeNull();
  });

  it('does not render a thumbnail for a non-previewable item, showing the plain type glyph instead', () => {
    const { container } = render(
      <HistoryRowComponent {...makeProps({ item: { ...printItem, status: 'failed' } })} />
    );
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('svg.lucide-printer')).not.toBeNull();
  });

  it('opens the preview when the thumbnail is clicked', async () => {
    const onPreview = vi.fn();
    const user = userEvent.setup();
    const { container } = render(<HistoryRowComponent {...makeProps({ onPreview })} />);

    await user.click(container.querySelector('img')!);

    expect(onPreview).toHaveBeenCalledWith(printItem);
  });
});
