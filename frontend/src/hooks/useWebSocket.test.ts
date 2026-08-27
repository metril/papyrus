import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { AxiosError } from 'axios';
import { useWebSocket } from './useWebSocket';
import api from '../api/client';
import { useAuthStore } from '../store/authStore';

vi.mock('../api/client', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })) },
}));

vi.mock('../store/authStore', () => ({
  useAuthStore: { getState: vi.fn(() => ({ signOut: vi.fn() })) },
}));

function make401Error(): AxiosError {
  const err = new AxiosError('Unauthorized');
  err.response = { status: 401 } as AxiosError['response'];
  return err;
}

/**
 * Minimal mock of the browser WebSocket, driven manually by tests via
 * `simulateOpen`/`simulateClose`/`simulateMessage` instead of a real
 * network round trip. `close()` intentionally does NOT synchronously call
 * `simulateClose()` -- a real WebSocket's close event always fires
 * asynchronously, and several tests below (the F17 stale-socket races)
 * depend on being able to control that timing precisely.
 */
class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;

  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
  });

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  simulateClose() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  simulateMessage(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

function latestSocket(): MockWebSocket {
  return MockWebSocket.instances[MockWebSocket.instances.length - 1];
}

// Reassigned fresh in beforeEach and read by useAuthStore.getState's mock
// implementation below — tests grab it directly rather than re-deriving it
// from the mock's call args.
let signOutSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.mocked(api.get).mockClear();
  vi.mocked(api.get).mockImplementation(() => Promise.resolve({ data: {} }));
  signOutSpy = vi.fn();
  vi.mocked(useAuthStore.getState).mockReturnValue(
    { signOut: signOutSpy } as unknown as ReturnType<typeof useAuthStore.getState>,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('useWebSocket', () => {
  it('opens a socket derived from the given url and reports connected on open', () => {
    const { result } = renderHook(() => useWebSocket({ url: '/api/system/ws/jobs' }));

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(latestSocket().url).toContain('/api/system/ws/jobs');
    expect(result.current.connected).toBe(false);

    act(() => latestSocket().simulateOpen());
    expect(result.current.connected).toBe(true);
  });

  it('forwards parsed messages to onMessage and ignores non-JSON frames', () => {
    const onMessage = vi.fn();
    renderHook(() => useWebSocket({ url: '/x', onMessage }));
    const ws = latestSocket();

    act(() => ws.simulateOpen());
    act(() => ws.simulateMessage({ type: 'job_created', data: { id: 1 } }));
    expect(onMessage).toHaveBeenCalledWith({ type: 'job_created', data: { id: 1 } });

    act(() => ws.onmessage?.({ data: 'not json' }));
    expect(onMessage).toHaveBeenCalledTimes(1);
  });

  it('sets connected false when the socket closes', () => {
    const { result } = renderHook(() => useWebSocket({ url: '/x' }));
    const ws = latestSocket();
    act(() => ws.simulateOpen());
    expect(result.current.connected).toBe(true);

    act(() => ws.simulateClose());
    expect(result.current.connected).toBe(false);
  });

  it('does not open a socket at all when url is null', () => {
    renderHook(() => useWebSocket({ url: null }));
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  describe('reconnect backoff (F84)', () => {
    it('caps the delay at 30s and keeps retrying well past the old 10-attempt ceiling', () => {
      vi.useFakeTimers();
      renderHook(() => useWebSocket({ url: '/x', reconnectInterval: 1000 }));

      // 1000, 2000, 4000, 8000, 16000, then capped at 30000 from here on.
      // 12 consecutive failures is more than the old maxReconnectAttempts
      // (10) -- the fix removes that ceiling entirely.
      const expectedDelays = [
        1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000, 30000, 30000, 30000, 30000,
      ];

      for (const delay of expectedDelays) {
        const before = MockWebSocket.instances.length;
        act(() => latestSocket().simulateClose());

        act(() => {
          vi.advanceTimersByTime(delay - 1);
        });
        expect(MockWebSocket.instances.length).toBe(before);

        act(() => {
          vi.advanceTimersByTime(1);
        });
        expect(MockWebSocket.instances.length).toBe(before + 1);
      }
    });

    it('resets the backoff counter after a successful open', () => {
      vi.useFakeTimers();
      renderHook(() => useWebSocket({ url: '/x', reconnectInterval: 1000 }));

      act(() => latestSocket().simulateClose());
      act(() => vi.advanceTimersByTime(1000)); // first retry: 1000ms delay
      expect(MockWebSocket.instances).toHaveLength(2);

      act(() => latestSocket().simulateOpen()); // succeeds -- counter resets
      act(() => latestSocket().simulateClose());

      const before = MockWebSocket.instances.length;
      act(() => vi.advanceTimersByTime(999));
      expect(MockWebSocket.instances.length).toBe(before); // not yet due
      act(() => vi.advanceTimersByTime(1));
      expect(MockWebSocket.instances.length).toBe(before + 1); // due at 1000ms again, not 2000ms
    });
  });

  describe('cleanup and stale-socket handling (F17)', () => {
    it('cleanup closes the socket and its close event schedules no reconnect', () => {
      vi.useFakeTimers();
      const { unmount } = renderHook(() => useWebSocket({ url: '/x' }));
      const ws = latestSocket();
      act(() => ws.simulateOpen());

      unmount();
      expect(ws.close).toHaveBeenCalledTimes(1);

      // The real close event still fires asynchronously after unmount.
      act(() => ws.simulateClose());
      act(() => vi.advanceTimersByTime(60_000));

      expect(MockWebSocket.instances).toHaveLength(1); // no reconnect socket
    });

    it('an orphaned socket from a url change never steals the ref or reconnects to the stale url', () => {
      vi.useFakeTimers();
      const { rerender } = renderHook(({ url }) => useWebSocket({ url }), {
        initialProps: { url: '/scan-a' },
      });
      const socketA = MockWebSocket.instances[0];
      act(() => socketA.simulateOpen());

      rerender({ url: '/scan-b' });
      expect(MockWebSocket.instances).toHaveLength(2);
      const socketB = MockWebSocket.instances[1];
      expect(socketB.url).toContain('/scan-b');

      // Socket A's close event arrives late, after B has already taken over.
      act(() => socketA.simulateClose());

      // No reconnect to the stale /scan-a url should ever be scheduled.
      act(() => vi.advanceTimersByTime(60_000));
      expect(MockWebSocket.instances).toHaveLength(2);
      expect(MockWebSocket.instances.every((s) => s.url !== socketA.url || s === socketA)).toBe(
        true,
      );

      // B is unaffected and still behaves normally afterwards.
      act(() => socketB.simulateOpen());
      act(() => socketB.simulateClose());
      const before = MockWebSocket.instances.length;
      act(() => vi.advanceTimersByTime(1000));
      expect(MockWebSocket.instances.length).toBe(before + 1);
    });
  });

  describe('wake reconnects (F84)', () => {
    it('reconnects immediately on window "online" when no socket is open', () => {
      vi.useFakeTimers();
      renderHook(() => useWebSocket({ url: '/x' }));
      act(() => latestSocket().simulateClose()); // socket gone, backoff timer pending

      expect(MockWebSocket.instances).toHaveLength(1);
      act(() => window.dispatchEvent(new Event('online')));
      expect(MockWebSocket.instances).toHaveLength(2);
    });

    it('reconnects on document visibilitychange to visible when no socket is open', () => {
      vi.useFakeTimers();
      renderHook(() => useWebSocket({ url: '/x' }));
      act(() => latestSocket().simulateClose());

      Object.defineProperty(document, 'visibilityState', {
        value: 'visible',
        configurable: true,
      });
      act(() => document.dispatchEvent(new Event('visibilitychange')));
      expect(MockWebSocket.instances).toHaveLength(2);
    });

    it('does not open a second socket on "online" when one is already open', () => {
      vi.useFakeTimers();
      renderHook(() => useWebSocket({ url: '/x' }));
      act(() => latestSocket().simulateOpen());

      act(() => window.dispatchEvent(new Event('online')));
      expect(MockWebSocket.instances).toHaveLength(1);
    });
  });

  describe('auth-failure probe (coordinator ruling on F48 x F17/F84)', () => {
    /** Fails the current socket and advances well past any possible backoff
     * delay (max 30s) so the next reconnect attempt has definitely fired. */
    function failAndAdvance() {
      act(() => latestSocket().simulateClose());
      act(() => vi.advanceTimersByTime(40_000));
    }

    it('does not probe /auth/me before the 3rd consecutive failed attempt', () => {
      vi.useFakeTimers();
      renderHook(() => useWebSocket({ url: '/x' }));

      failAndAdvance(); // attempt 1
      failAndAdvance(); // attempt 2
      expect(api.get).not.toHaveBeenCalled();
    });

    it('probes /auth/me on every 3rd consecutive failed attempt', () => {
      vi.useFakeTimers();
      renderHook(() => useWebSocket({ url: '/x' }));

      failAndAdvance(); // 1
      failAndAdvance(); // 2
      failAndAdvance(); // 3 -- probe
      expect(api.get).toHaveBeenCalledTimes(1);
      expect(api.get).toHaveBeenCalledWith('/auth/me');

      failAndAdvance(); // 4
      failAndAdvance(); // 5
      expect(api.get).toHaveBeenCalledTimes(1);
      failAndAdvance(); // 6 -- probe again
      expect(api.get).toHaveBeenCalledTimes(2);
    });

    it('resets the streak on a successful open, so the count starts over from 0', () => {
      vi.useFakeTimers();
      renderHook(() => useWebSocket({ url: '/x' }));

      failAndAdvance(); // 1
      failAndAdvance(); // 2
      act(() => latestSocket().simulateOpen()); // success -- streak resets

      failAndAdvance(); // 1 again (not 3) -- no probe yet
      failAndAdvance(); // 2 again
      expect(api.get).not.toHaveBeenCalled();

      failAndAdvance(); // 3 since the reset -- probe fires
      expect(api.get).toHaveBeenCalledTimes(1);
    });

    it('ignores a rejected /auth/me probe that is not a 401 (network blip, 5xx)', async () => {
      vi.useFakeTimers();
      vi.mocked(api.get).mockImplementation(() => Promise.reject(new Error('network error')));
      renderHook(() => useWebSocket({ url: '/x' }));

      failAndAdvance();
      failAndAdvance();
      failAndAdvance(); // probe fires and rejects

      expect(api.get).toHaveBeenCalledTimes(1);
      // Let the rejected promise's .catch() settle -- an unhandled
      // rejection would otherwise fail the test run.
      await act(async () => {
        await Promise.resolve();
      });

      expect(signOutSpy).not.toHaveBeenCalled();
    });

    it('signs out locally when the probe itself gets a 401 (F16: /auth/me is on the interceptor skip-list, so the interceptor never acts on it)', async () => {
      vi.useFakeTimers();
      vi.mocked(api.get).mockImplementation(() => Promise.reject(make401Error()));
      renderHook(() => useWebSocket({ url: '/x' }));

      failAndAdvance();
      failAndAdvance();
      failAndAdvance(); // probe fires and rejects with a 401

      await act(async () => {
        await Promise.resolve();
      });

      expect(signOutSpy).toHaveBeenCalledTimes(1);
    });
  });
});
