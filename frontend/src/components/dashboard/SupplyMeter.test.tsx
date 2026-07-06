import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import { useConnectionStore } from '../../store/connectionStore';
import SupplyMeter from './SupplyMeter';

function makeWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function serveMarkers(markers: { name: string; level: number; color: string }[]) {
  server.use(
    http.get('/api/printer/status', () =>
      HttpResponse.json({
        state: 3,
        state_message: 'Ready',
        accepting_jobs: true,
        markers,
        state_reasons: ['none'],
      }),
    ),
  );
}

describe('SupplyMeter thresholds', () => {
  beforeEach(() => {
    useConnectionStore.setState({ printersConnected: false });
  });

  it('paints a level ≥20% with the green status fill and shows no low warning', async () => {
    serveMarkers([{ name: 'Black Toner', level: 25, color: 'black' }]);
    const { container } = render(<SupplyMeter />, { wrapper: makeWrapper() });

    expect(await screen.findByText('25%')).toBeInTheDocument();
    expect(container.querySelector('.bg-green-600')).not.toBeNull();
    expect(container.querySelector('.bg-amber-500')).toBeNull();
    expect(screen.queryByText('Low')).not.toBeInTheDocument();
    expect(screen.queryByText('Critical')).not.toBeInTheDocument();
  });

  it('paints a level <20% amber with a "Low" icon+label', async () => {
    serveMarkers([{ name: 'Black Toner', level: 15, color: 'black' }]);
    const { container } = render(<SupplyMeter />, { wrapper: makeWrapper() });

    expect(await screen.findByText('15%')).toBeInTheDocument();
    expect(container.querySelector('.bg-amber-500')).not.toBeNull();
    expect(container.querySelector('.bg-green-600')).toBeNull();
    expect(screen.getByText('Low')).toBeInTheDocument();
  });

  it('paints a level <10% red with a "Critical" icon+label', async () => {
    serveMarkers([{ name: 'Black Toner', level: 5, color: 'black' }]);
    const { container } = render(<SupplyMeter />, { wrapper: makeWrapper() });

    expect(await screen.findByText('5%')).toBeInTheDocument();
    expect(container.querySelector('.bg-red-600')).not.toBeNull();
    expect(screen.getByText('Critical')).toBeInTheDocument();
  });

  it('shows a quiet "Unknown" with no fill for a negative level', async () => {
    serveMarkers([{ name: 'Drum Unit', level: -1, color: 'none' }]);
    const { container } = render(<SupplyMeter />, { wrapper: makeWrapper() });

    expect(await screen.findByText('Unknown')).toBeInTheDocument();
    expect(container.querySelector('[data-status]')).toBeNull();
  });

  it('renders an empty state when the printer reports no markers', async () => {
    serveMarkers([]);
    render(<SupplyMeter />, { wrapper: makeWrapper() });

    expect(await screen.findByText('No supply data reported')).toBeInTheDocument();
  });
});
