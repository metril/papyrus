import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { server } from '../../test/mocks/server';
import ScannersCard from './ScannersCard';

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

async function openNativeAdd() {
  const user = userEvent.setup();
  server.use(http.get('/api/scanners', () => HttpResponse.json([])));
  render(<ScannersCard />, { wrapper: wrapper() });
  await user.click(await screen.findByText('+ Add scanner'));
  await user.click(screen.getByRole('button', { name: 'Brother' }));
  return user;
}

describe('ScannersCard native driver', () => {
  it('defaults to the native driver and builds a brothernet device from a typed IP', async () => {
    const user = await openNativeAdd();
    expect(screen.getByRole('button', { name: 'Native' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText('Model')).not.toBeInTheDocument();

    await user.type(screen.getByPlaceholderText('Brother DCP-L2540DW'), 'Office');
    expect(screen.getByRole('button', { name: 'Add scanner' })).toBeDisabled();
    await user.type(screen.getByLabelText('IP address'), '10.0.0.5');

    expect(screen.getByText('brothernet:10.0.0.5')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add scanner' })).toBeEnabled();
  });

  it('rejects an address that is not a host', async () => {
    const user = await openNativeAdd();
    await user.type(screen.getByPlaceholderText('Brother DCP-L2540DW'), 'Office');
    await user.type(screen.getByLabelText('IP address'), '10.0.0.5:54921');
    expect(screen.getByText('Enter a valid IP address or hostname.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add scanner' })).toBeDisabled();
  });

  it('adds a native scanner without brscan4 config', async () => {
    let posted: Record<string, unknown> | null = null;
    server.use(
      http.post('/api/scanners', async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 1, ...posted }, { status: 201 });
      }),
    );
    const user = await openNativeAdd();
    await user.type(screen.getByPlaceholderText('Brother DCP-L2540DW'), 'Office');
    await user.type(screen.getByLabelText('IP address'), '10.0.0.5');
    await user.click(screen.getByRole('button', { name: 'Add scanner' }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ name: 'Office', device: 'brothernet:10.0.0.5' });
    expect(posted).not.toHaveProperty('post_scan_config');
  });

  it('lists discovered devices, selecting a supported one and disabling untested models', async () => {
    server.use(
      http.get('/api/scanners/discover', () =>
        HttpResponse.json([
          { device: 'brother4:net1;dev0', description: 'Brother DCP' },
          {
            device: 'brothernet:10.0.0.5',
            description: 'DCP-L2540DW series (native Brother driver)',
            driver: 'brothernet',
            supported: true,
            ip: '10.0.0.5',
            busy: false,
          },
          {
            device: null,
            description: 'MFC-L9999CDW (untested with the native driver, cannot be added)',
            driver: 'brothernet',
            supported: false,
            ip: '10.0.0.9',
            busy: false,
          },
        ]),
      ),
    );
    const user = await openNativeAdd();
    await user.click(screen.getByRole('button', { name: 'Scan network' }));

    const supported = await screen.findByRole('button', { name: /10\.0\.0\.5/ });
    const untested = screen.getByRole('button', { name: /10\.0\.0\.9/ });
    expect(untested).toBeDisabled();
    expect(screen.queryByText('brother4:net1;dev0')).not.toBeInTheDocument();

    await user.click(supported);
    expect(screen.getByLabelText('IP address')).toHaveValue('10.0.0.5');
    expect(screen.getByPlaceholderText('Brother DCP-L2540DW')).toHaveValue('DCP-L2540DW series');
    expect(screen.getByText('brothernet:10.0.0.5')).toBeInTheDocument();
  });

  it('keeps the brscan4 registration flow behind the toggle', async () => {
    const user = await openNativeAdd();
    await user.click(screen.getByRole('button', { name: 'brscan4' }));
    expect(screen.getByText('Model')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Register' })).toBeInTheDocument();
  });
});
