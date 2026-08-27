import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import UploadForm from './UploadForm';
import { useToastStore } from '../../store/toastStore';
import * as printerApi from '../../api/printer';
import type { PrintJob } from '../../types';

// F86/F89 need to control exactly when/how the upload call resolves per
// file. Routing that through a real multipart POST (msw + axios + jsdom's
// XHR/FormData) hangs indefinitely in this test environment rather than
// resolving or rejecting, so `uploadPrintJob` is mocked directly instead —
// the dedupe tests below don't touch the network at all and are unaffected.
vi.mock('../../api/printer', async () => {
  const actual = await vi.importActual<typeof import('../../api/printer')>('../../api/printer');
  return { ...actual, uploadPrintJob: vi.fn() };
});

const uploadPrintJobMock = vi.mocked(printerApi.uploadPrintJob);

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function makeFile(name: string, opts: { size?: number; lastModified?: number } = {}): File {
  return new File(['x'.repeat(opts.size ?? 10)], name, {
    type: 'application/pdf',
    lastModified: opts.lastModified ?? 1,
  });
}

function getFileInput(container: HTMLElement): HTMLInputElement {
  const input = container.querySelector('input[type="file"]');
  if (!input) throw new Error('no file input found');
  return input as HTMLInputElement;
}

function makeJob(overrides: Partial<PrintJob> = {}): PrintJob {
  return {
    id: 1,
    user_id: null,
    cups_job_id: null,
    title: 'x',
    filename: 'x.pdf',
    file_size: 10,
    mime_type: 'application/pdf',
    status: 'held',
    copies: 1,
    duplex: false,
    media: 'A4',
    source_type: 'upload',
    printer_id: null,
    has_pin: false,
    error_message: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    completed_at: null,
    ...overrides,
  };
}

describe('UploadForm', () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
    uploadPrintJobMock.mockReset();
  });

  it('dedupes by name+size+lastModified and toasts once when a duplicate is skipped (F151)', async () => {
    const user = userEvent.setup();
    const { container } = render(<UploadForm />, { wrapper: makeWrapper() });

    const fileA = makeFile('scan.pdf', { size: 10, lastModified: 111 });
    await user.upload(getFileInput(container), [fileA]);
    await waitFor(() => expect(screen.getByText(/scan\.pdf/)).toBeInTheDocument());

    // Same name+size+lastModified as fileA — a true duplicate, even though
    // it's a distinct File instance (e.g. re-selected from a picker).
    const duplicate = makeFile('scan.pdf', { size: 10, lastModified: 111 });
    await user.upload(getFileInput(container), [duplicate]);

    expect(screen.getAllByText(/scan\.pdf/)).toHaveLength(1);
    expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
      'Skipped 1 file already in the queue',
    );
  });

  it('does not dedupe two files that merely share a name (different size/lastModified)', async () => {
    const user = userEvent.setup();
    const { container } = render(<UploadForm />, { wrapper: makeWrapper() });

    await user.upload(getFileInput(container), [makeFile('scan.pdf', { size: 10, lastModified: 111 })]);
    await user.upload(getFileInput(container), [makeFile('scan.pdf', { size: 20, lastModified: 222 })]);

    expect(screen.getAllByText(/scan\.pdf/)).toHaveLength(2);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('removes only the file that already succeeded when a later file in the batch fails (F86)', async () => {
    uploadPrintJobMock.mockImplementation(async (file) => {
      if (file.name === 'good.pdf') return makeJob({ filename: 'good.pdf' });
      throw new Error('File too large');
    });

    const user = userEvent.setup();
    const { container } = render(<UploadForm />, { wrapper: makeWrapper() });

    const good = makeFile('good.pdf', { lastModified: 1 });
    const bad = makeFile('bad.pdf', { lastModified: 2 });
    await user.upload(getFileInput(container), [good, bad]);
    await waitFor(() => expect(screen.getByText(/good\.pdf/)).toBeInTheDocument());
    expect(screen.getByText(/bad\.pdf/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /upload & hold/i }));

    await waitFor(() => expect(screen.queryByText(/good\.pdf/)).not.toBeInTheDocument());
    // The failed file stays queued instead of vanishing along with the
    // successful one — and isn't silently re-sent on a retry.
    expect(screen.getByText(/bad\.pdf/)).toBeInTheDocument();
    expect(screen.getByText('File too large')).toBeInTheDocument();
    expect(uploadPrintJobMock).toHaveBeenCalledTimes(2);
  });

  it('clamps an out-of-range copies value and never submits 0 or >99 (F89)', async () => {
    uploadPrintJobMock.mockResolvedValue(makeJob());

    const user = userEvent.setup();
    const { container } = render(<UploadForm />, { wrapper: makeWrapper() });

    await user.upload(getFileInput(container), [makeFile('x.pdf')]);
    await waitFor(() => expect(screen.getByText(/x\.pdf/)).toBeInTheDocument());

    const copiesInput = container.querySelector('input[type="number"]') as HTMLInputElement;
    await user.clear(copiesInput);
    await user.type(copiesInput, '500');
    // Clamped in the UI as soon as it goes out of range, well before submit.
    expect(copiesInput).toHaveValue(99);

    await user.click(screen.getByRole('button', { name: /upload & hold/i }));

    await waitFor(() => expect(uploadPrintJobMock).toHaveBeenCalledTimes(1));
    expect(uploadPrintJobMock).toHaveBeenCalledWith(
      expect.any(File),
      expect.objectContaining({ copies: 99 }),
    );
  });

  it('treats a cleared copies field as 1 rather than sending 0 (F89)', async () => {
    uploadPrintJobMock.mockResolvedValue(makeJob());

    const user = userEvent.setup();
    const { container } = render(<UploadForm />, { wrapper: makeWrapper() });

    await user.upload(getFileInput(container), [makeFile('x.pdf')]);
    await waitFor(() => expect(screen.getByText(/x\.pdf/)).toBeInTheDocument());

    const copiesInput = container.querySelector('input[type="number"]') as HTMLInputElement;
    await user.clear(copiesInput);
    expect(copiesInput).toHaveValue(null); // field stays visually empty while editing

    await user.click(screen.getByRole('button', { name: /upload & hold/i }));

    await waitFor(() => expect(uploadPrintJobMock).toHaveBeenCalledTimes(1));
    expect(uploadPrintJobMock).toHaveBeenCalledWith(
      expect.any(File),
      expect.objectContaining({ copies: 1 }),
    );
  });
});
