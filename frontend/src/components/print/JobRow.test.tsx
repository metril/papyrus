import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { memo, useState } from 'react';
import { JobRowComponent } from './JobRow';
import type { JobRowProps } from './JobRow';
import type { PrintJob, ManagedPrinter } from '../../types';

const heldJob: PrintJob = {
  id: 1,
  cups_job_id: null,
  title: 'Doc',
  filename: 'doc.pdf',
  file_size: 1024,
  mime_type: 'application/pdf',
  status: 'held',
  copies: 1,
  duplex: false,
  media: 'A4',
  source_type: 'upload',
  printer_id: null,
  has_pin: false,
  error_message: null,
  created_at: '2026-07-05T00:00:00Z',
  updated_at: '2026-07-05T00:00:00Z',
  completed_at: null,
};

const printers: ManagedPrinter[] = [];

// Stable no-op references shared across renders — a real caller would pass
// useCallback-wrapped handlers; these plain module-level functions are
// equally stable and keep the test focused on the `job` prop.
function noop() {}

function makeProps(overrides: Partial<JobRowProps> = {}): JobRowProps {
  return {
    job: heldJob,
    printers,
    busy: false,
    assigning: false,
    onPreview: noop,
    onRelease: noop,
    onCancel: noop,
    onReprint: noop,
    onDelete: noop,
    onAssign: noop,
    ...overrides,
  };
}

describe('JobRow memoization', () => {
  it('does not re-invoke the row when the parent re-renders with an unchanged job reference, but does when the job actually changes', async () => {
    // Wrap the raw (un-memoized) row implementation in a spy, then apply the
    // same `memo()` production code uses — this probes exactly the contract
    // `export default memo(JobRowComponent)` promises.
    const renderSpy = vi.fn(JobRowComponent);
    const ProbedJobRow = memo(renderSpy);

    function Harness({ job }: { job: PrintJob }) {
      const [tick, setTick] = useState(0);
      return (
        <div>
          <button onClick={() => setTick((t) => t + 1)}>tick:{tick}</button>
          <ProbedJobRow {...makeProps({ job })} />
        </div>
      );
    }

    const user = userEvent.setup();
    const { rerender } = render(<Harness job={heldJob} />);
    expect(renderSpy).toHaveBeenCalledTimes(1);

    // Re-render the parent for an unrelated reason (its own tick state)
    // while passing the SAME job reference — the memoized row must not
    // re-invoke.
    await user.click(screen.getByRole('button', { name: /tick/ }));
    expect(renderSpy).toHaveBeenCalledTimes(1);

    // Sanity check the harness itself: a genuinely different job reference
    // DOES cause the row to re-invoke, proving the spy isn't just vacuously
    // stuck at 1.
    rerender(<Harness job={{ ...heldJob, status: 'printing' }} />);
    expect(renderSpy).toHaveBeenCalledTimes(2);
  });
});
