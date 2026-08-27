import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChunkErrorBoundary from './ChunkErrorBoundary';

const RELOAD_GUARD_KEY = 'papyrus:chunk-reload';
const CHUNK_ERROR_MESSAGE = 'Failed to fetch dynamically imported module: /assets/history.js';

function Bomb({ message }: { message: string }): never {
  throw new Error(message);
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

  it('reloads once on a chunk-load error and sets the sessionStorage guard', () => {
    render(
      <ChunkErrorBoundary>
        <Bomb message={CHUNK_ERROR_MESSAGE} />
      </ChunkErrorBoundary>,
    );

    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(RELOAD_GUARD_KEY)).toBe('1');
  });

  it('does not reload a second time if the guard is already set (no reload loop)', () => {
    sessionStorage.setItem(RELOAD_GUARD_KEY, '1');

    render(
      <ChunkErrorBoundary>
        <Bomb message={CHUNK_ERROR_MESSAGE} />
      </ChunkErrorBoundary>,
    );

    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it('does not reload for an unrelated render error', () => {
    render(
      <ChunkErrorBoundary>
        <Bomb message="TypeError: cannot read property of undefined" />
      </ChunkErrorBoundary>,
    );

    expect(reloadSpy).not.toHaveBeenCalled();
  });

  // Review fix #2: previously rendered `null` for ANY error, chunk or not,
  // so a non-chunk render bug permanently blanked the content area with no
  // feedback and no way out.
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
      sessionStorage.setItem(RELOAD_GUARD_KEY, '1');
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

    // Review fix #2: recovery via remount — AppShell keys the boundary on
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

  // Review fix #1: the guard used to be set-once-forever, so only the
  // *first* stale-chunk incident in a tab's lifetime ever recovered — a
  // second deploy hitting the same still-open tab found the guard already
  // set and silently gave up (no reload, blank fallback).
  describe('reload guard lifecycle across incidents', () => {
    it('clears the guard once the boundary mounts cleanly (no error)', () => {
      sessionStorage.setItem(RELOAD_GUARD_KEY, '1');

      render(
        <ChunkErrorBoundary>
          <div>content</div>
        </ChunkErrorBoundary>,
      );

      expect(sessionStorage.getItem(RELOAD_GUARD_KEY)).toBeNull();
    });

    it('does NOT clear the guard on a mount that immediately re-catches the same chunk error (no reload loop)', () => {
      sessionStorage.setItem(RELOAD_GUARD_KEY, '1');

      render(
        <ChunkErrorBoundary>
          <Bomb message={CHUNK_ERROR_MESSAGE} />
        </ChunkErrorBoundary>,
      );

      expect(reloadSpy).not.toHaveBeenCalled();
      expect(sessionStorage.getItem(RELOAD_GUARD_KEY)).toBe('1');
    });

    it('reloads again for a second incident, once a successful mount happened in between', () => {
      // Incident 1: reloads and sets the guard (simulating the actual reload).
      const first = render(
        <ChunkErrorBoundary>
          <Bomb message={CHUNK_ERROR_MESSAGE} />
        </ChunkErrorBoundary>,
      );
      expect(reloadSpy).toHaveBeenCalledTimes(1);
      expect(sessionStorage.getItem(RELOAD_GUARD_KEY)).toBe('1');
      first.unmount();

      // Post-reload: a fresh mount renders cleanly (new chunks loaded fine),
      // clearing the guard.
      const second = render(
        <ChunkErrorBoundary>
          <div>healthy again</div>
        </ChunkErrorBoundary>,
      );
      expect(sessionStorage.getItem(RELOAD_GUARD_KEY)).toBeNull();
      second.unmount();

      // Incident 2 (a later deploy hits the same tab): must reload again,
      // not silently give up because of a guard from incident 1.
      render(
        <ChunkErrorBoundary>
          <Bomb message={CHUNK_ERROR_MESSAGE} />
        </ChunkErrorBoundary>,
      );
      expect(reloadSpy).toHaveBeenCalledTimes(2);
    });
  });
});
