import { describe, it, expect, vi } from 'vitest';
import { cloneElement, type ReactElement } from 'react';
import { render, screen } from '@testing-library/react';
import type { TrendPoint } from '../../api/admin';
import TrendChart from './TrendChart';

// jsdom gives <ResponsiveContainer> a zero size, so Recharts renders nothing.
// Replace it with a pass-through that injects a fixed size onto the chart.
// (vi.mock is hoisted above the imports, so TrendChart sees the patched module.)
vi.mock('recharts', async () => {
  const actual = await vi.importActual<typeof import('recharts')>('recharts');
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement }) =>
      cloneElement(children as ReactElement<{ width?: number; height?: number }>, {
        width: 500,
        height: 300,
      }),
  };
});

const DATA: TrendPoint[] = [
  { date: '2026-06-07', prints: 3, scans: 1 },
  { date: '2026-06-08', prints: 5, scans: 2 },
  { date: '2026-06-09', prints: 2, scans: 4 },
];

describe('TrendChart', () => {
  it('renders two series lines from the fixture data', () => {
    const { container } = render(<TrendChart data={DATA} />);
    // One <path class="recharts-line-curve"> per Line — prints and scans.
    expect(container.querySelectorAll('.recharts-line-curve')).toHaveLength(2);
  });

  it('renders a legend naming both series in text tokens', () => {
    render(<TrendChart data={DATA} />);
    expect(screen.getAllByText('Prints').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Scans').length).toBeGreaterThanOrEqual(1);
  });

  it('exposes an sr-only data table mirroring the series', () => {
    render(<TrendChart data={DATA} />);
    expect(screen.getByText('Prints and scans per day over the last 30 days')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Prints' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Scans' })).toBeInTheDocument();
  });
});
