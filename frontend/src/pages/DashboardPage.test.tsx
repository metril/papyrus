import { describe, it, expect, beforeEach, vi } from 'vitest';
import { cloneElement, type ReactElement, type ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useConnectionStore } from '../store/connectionStore';
import DashboardPage from './DashboardPage';

// See TrendChart.test — give ResponsiveContainer a real size under jsdom.
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

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe('DashboardPage', () => {
  beforeEach(() => {
    useConnectionStore.setState({ printersConnected: true });
  });

  it('renders stat tiles, all three chart views, and their sr-only tables from the stats fixture', async () => {
    const { container } = render(<DashboardPage />, { wrapper: makeWrapper() });

    // Hero tiles (loads after the stats query resolves).
    expect(await screen.findByText('Total Prints')).toBeInTheDocument();
    expect(screen.getByText('Total Scans')).toBeInTheDocument();

    // TrendChart: two series lines + its sr-only table.
    expect(container.querySelectorAll('.recharts-line-curve')).toHaveLength(2);
    expect(screen.getByText('Prints and scans per day over the last 30 days')).toBeInTheDocument();

    // UserChart: per-user rows (axis tick + sr-only table) + its sr-only table.
    expect(screen.getAllByText('alice').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Print and scan totals per user')).toBeInTheDocument();

    // SupplyMeter: marker from the default printer-status fixture.
    expect(await screen.findByText('Black Toner')).toBeInTheDocument();
    expect(screen.getByText('64%')).toBeInTheDocument();
  });
});
