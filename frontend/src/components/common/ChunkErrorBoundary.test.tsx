import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChunkErrorBoundary from './ChunkErrorBoundary';

const RELOAD_GUARD_KEY = 'papyrus:chunk-reload';

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
        <Bomb message="Failed to fetch dynamically imported module: /assets/history.js" />
      </ChunkErrorBoundary>,
    );

    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem(RELOAD_GUARD_KEY)).toBe('1');
  });

  it('does not reload a second time if the guard is already set (no reload loop)', () => {
    sessionStorage.setItem(RELOAD_GUARD_KEY, '1');

    render(
      <ChunkErrorBoundary>
        <Bomb message="Failed to fetch dynamically imported module: /assets/history.js" />
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
});
