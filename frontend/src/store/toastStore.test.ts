import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { showToast, useToastStore } from './toastStore';

describe('toastStore', () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('showToast from a plain module adds a toast (default type error)', () => {
    showToast('hello');

    const { toasts } = useToastStore.getState();
    expect(toasts).toHaveLength(1);
    expect(toasts[0].message).toBe('hello');
    expect(toasts[0].type).toBe('error');
  });

  it('auto-dismisses the toast after 4000ms', () => {
    vi.useFakeTimers();

    showToast('bye');
    expect(useToastStore.getState().toasts).toHaveLength(1);

    vi.advanceTimersByTime(3999);
    expect(useToastStore.getState().toasts).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });
});
