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

  it('dismiss clears the pending auto-hide timer instead of leaving it to fire later (F145)', () => {
    vi.useFakeTimers();
    const clearSpy = vi.spyOn(globalThis, 'clearTimeout');

    showToast('early exit');
    const { id } = useToastStore.getState().toasts[0];
    useToastStore.getState().dismiss(id);

    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(useToastStore.getState().toasts).toHaveLength(0);

    // The (now-cleared) timer must not still be pending — advancing past
    // its original 4000ms must not trigger any further store update. There
    // is nothing left to remove, but a stray `set` firing here would be
    // exactly the wasted-render bug (F145) this fix removes.
    const setSpy = vi.spyOn(useToastStore, 'setState');
    vi.advanceTimersByTime(4000);
    expect(setSpy).not.toHaveBeenCalled();
  });

  it('dismissing an already-fired toast (unknown id) is a no-op, not a crash', () => {
    expect(() => useToastStore.getState().dismiss(999999)).not.toThrow();
  });
});
