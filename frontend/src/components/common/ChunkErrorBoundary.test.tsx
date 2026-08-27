import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Suspense, lazy } from 'react';
import ChunkErrorBoundary from './ChunkErrorBoundary';

const RELOAD_GUARD_KEY = 'papyrus:chunk-reload';
const RELOAD_GUARD_TTL_MS = 30_000;
const CHUNK_ERROR_MESSAGE = 'Failed to fetch dynamically imported module: /assets/history.js';

function Bomb({ message }: { message: string }): never {
  throw new Error(message);
}

/** Fresh timestamp, well inside the 30s TTL. */
function freshGuardTimestamp(): string {
  return String(Date.now());
}

/** Older than the 30s TTL. */
function staleGuardTimestamp(): string {
  return String(Date.now() - (RELOAD_GUARD_TTL_MS + 1_000));
}

describe('ChunkErrorBoundary (F101)', () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;
  let reloadSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    sessionStorage.removeItem(RELOAD_GUARD_KEY);
    // React logs the caught error to console.error even inside a boundary —
    // expected noise for these tests specifically, suppressed so it doesn't
    // pollute the overall test run's output.
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    reloadSpy = vi.fn();
    Object.defineProperty(window, 'location', {
      value: { ...window.location, reload: reloadSpy },
      writable: true,
    });
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  it('renders children when nothing throws', () => {
    render(
      <ChunkErrorBoundary>
        <div>content</div>
      </ChunkErrorBoundary>,
    );
    expect(screen.getByText('content')).toBeInTheDocument();
  });

  it('reloads once on a chunk-load error and writes a fresh timestamp guard', () => {
    render(
      <ChunkErrorBoundary>
        <Bomb message={CHUNK_ERROR_MESSAGE} />
      </ChunkErrorBoundary>,
    );

    expect(reloadSpy).toHaveBeenCalledTimes(1);
    const stored = Number(sessionStorage.getItem(RELOAD_GUARD_KEY));
    expect(Number.isFinite(stored)).toBe(true);
    expect(Date.now() - stored).toBeLessThan(1_000);
  });

  it('does not reload a second time while the guard is still fresh (<30s) — no reload loop', () => {
    sessionStorage.setItem(RELOAD_GUARD_KEY, freshGuardTimestamp());

    render(
      <ChunkErrorBoundary>
        <Bomb message={CHUNK_ERROR_MESSAGE} />
      </ChunkErrorBoundary>,
    );

    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it('reloads again once the guard is older than 30s', () => {
    sessionStorage.setItem(RELOAD_GUARD_KEY, staleGuardTimestamp());

    render(
      <ChunkErrorBoundary>
        <Bomb message={CHUNK_ERROR_MESSAGE} />
      </ChunkErrorBoundary>,
    );

    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it('does not reload for an unrelated render error', () => {
    render(
      <ChunkErrorBoundary>
        <Bomb message="TypeError: cannot read property of undefined" />
      </ChunkErrorBoundary>,
    );

    expect(reloadSpy).not.toHaveBeenCalled();
  });

  // Review fix #2 (prior round): previously rendered `null` for ANY error,
  // chunk or not, so a non-chunk render bug permanently blanked the content
  // area with no feedback and no way out.
  describe('non-chunk render errors', () => {
    it('renders a visible fallback instead of a blank null', () => {
      render(
        <ChunkErrorBoundary>
          <Bomb message="TypeError: cannot read property of undefined" />
        </ChunkErrorBoundary>,
      );

      expect(screen.getByText('Something went wrong')).toBeInTheDocument();
      expect(screen.getByText('This page ran into a problem.')).toBeInTheDocument();
    });

    it('"Try again" clears the guard and reloads', async () => {
      sessionStorage.setItem(RELOAD_GUARD_KEY, freshGuardTimestamp());
      const user = userEvent.setup();
      render(
        <ChunkErrorBoundary>
          <Bomb message="TypeError: boom" />
        </ChunkErrorBoundary>,
      );

      await user.click(screen.getByRole('button', { name: 'Try again' }));

      expect(reloadSpy).toHaveBeenCalledTimes(1);
      expect(sessionStorage.getItem(RELOAD_GUARD_KEY)).toBeNull();
    });

    // Recovery via remount — AppShell keys the boundary on
    // `location.pathname`, so navigating to a different route (React
    // unmounts the old instance, mounts a fresh one) must actually recover
    // rather than staying wedged in the error state forever.
    it('navigating away (a fresh remount) recovers instead of staying wedged in the error state', () => {
      const { unmount } = render(
        <ChunkErrorBoundary>
          <Bomb message="TypeError: boom" />
        </ChunkErrorBoundary>,
      );
      expect(screen.getByText('Something went wrong')).toBeInTheDocument();
      unmount();

      // A different route (a fresh ChunkErrorBoundary instance, exactly
      // what `key={location.pathname}` produces on navigation) renders its
      // own content cleanly.
      render(
        <ChunkErrorBoundary>
          <div>a different route's content</div>
        </ChunkErrorBoundary>,
      );

      expect(screen.getByText("a different route's content")).toBeInTheDocument();
      expect(screen.queryByText('Something went wrong')).not.toBeInTheDocument();
    });
  });

  // Review fix (critical regression): the real failure mode is a
  // React.lazy route whose dynamic import rejects — the boundary sits
  // *above* <Suspense>, so a fresh page load commits the Suspense fallback
  // (a clean render, nothing thrown) before the import's rejection is even
  // known. The previous "clear the guard on a clean mount" fix cleared it
  // on every single load because of exactly this ordering, so a
  // persistently broken chunk reloaded on every load forever. These tests
  // reproduce that real scenario end to end (synchronous `Bomb` throws
  // above cannot: they never go through a clean-mount-then-catch cycle).
  describe('the real React.lazy + Suspense rejection scenario', () => {
    function renderLazyFailure() {
      const Bomb2 = lazy(() => Promise.reject(new Error(CHUNK_ERROR_MESSAGE)));
      return render(
        <ChunkErrorBoundary>
          <Suspense fallback={<div>loading…</div>}>
            <Bomb2 />
          </Suspense>
        </ChunkErrorBoundary>,
      );
    }

    it('reloads once on the first load (no guard) and writes a fresh timestamp guard', async () => {
      renderLazyFailure();

      await waitFor(() => expect(reloadSpy).toHaveBeenCalledTimes(1));
      const stored = Number(sessionStorage.getItem(RELOAD_GUARD_KEY));
      expect(Number.isFinite(stored)).toBe(true);
      expect(Date.now() - stored).toBeLessThan(1_000);
    });

    it('does NOT reload again with a fresh (<30s) guard already set — renders the fallback instead', async () => {
      sessionStorage.setItem(RELOAD_GUARD_KEY, freshGuardTimestamp());

      renderLazyFailure();

      await waitFor(() => expect(screen.getByText('Something went wrong')).toBeInTheDocument());
      expect(reloadSpy).not.toHaveBeenCalled();
    });

    it('reloads again once the guard is older than 30s', async () => {
      sessionStorage.setItem(RELOAD_GUARD_KEY, staleGuardTimestamp());

      renderLazyFailure();

      await waitFor(() => expect(reloadSpy).toHaveBeenCalledTimes(1));
    });

    it('three consecutive "page loads" of a persistently failing chunk: only the first reloads (bounded, not an infinite loop)', async () => {
      // Load 1: nothing in sessionStorage yet — reloads, writes the guard.
      const first = renderLazyFailure();
      await waitFor(() => expect(reloadSpy).toHaveBeenCalledTimes(1));
      first.unmount();

      // Load 2: simulates the reload that just happened landing on the
      // same still-broken chunk. A real reload navigates to a fresh page
      // (a brand new ChunkErrorBoundary instance, same as this remount);
      // the guard written by load 1 is still fresh, so this must NOT
      // reload again, or a broken deploy hard-loops the tab forever.
      const second = renderLazyFailure();
      await waitFor(() => expect(screen.getByText('Something went wrong')).toBeInTheDocument());
      expect(reloadSpy).toHaveBeenCalledTimes(1);
      second.unmount();

      // Load 3: same story — still bounded to the one reload from load 1.
      const third = renderLazyFailure();
      await waitFor(() => expect(screen.getByText('Something went wrong')).toBeInTheDocument());
      expect(reloadSpy).toHaveBeenCalledTimes(1);
      third.unmount();
    });
  });
});
