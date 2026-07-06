import { describe, it, expect, vi, afterEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ToastProvider } from './Toast';
import { useToast } from '../../hooks/useToast';

function ShowButton() {
  const { show } = useToast();
  return <button onClick={() => show('boom')}>Show</button>;
}

describe('ToastProvider', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows a message and auto-dismisses after 4000ms', async () => {
    vi.useFakeTimers();
    const user = userEvent.setup({ delay: null, advanceTimers: vi.advanceTimersByTime });

    render(
      <ToastProvider>
        <ShowButton />
      </ToastProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'Show' }));

    expect(screen.getByText('boom')).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });

    expect(screen.queryByText('boom')).not.toBeInTheDocument();
  });
});
