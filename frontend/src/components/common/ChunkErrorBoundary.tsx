import { Component, type ReactNode } from 'react';
import ErrorState from './ErrorState';

// F101: a deploy replaces `static/` wholesale and the service worker no
// longer force-activates immediately (registerType 'prompt', no
// skipWaiting/clientsClaim — vite.config.ts), but a tab left open across a
// deploy can still hold a route that was never precached under the new
// build's hashed filenames. Its lazy import then 404s/soft-404s (the
// backend serves index.html for any unknown /assets/* path, and an HTML
// response fails the browser's module MIME check) and React.lazy's
// dynamic import rejects — with no error boundary anywhere in the app, that
// blanked the whole page. Reloading once picks up the fresh index.html and
// asset manifest; the sessionStorage guard stops a genuinely broken deploy
// (not just a stale tab) from reload-looping forever.
const RELOAD_GUARD_KEY = 'papyrus:chunk-reload';

function isChunkLoadError(error: unknown): boolean {
  const err = error as { name?: unknown; message?: unknown } | null | undefined;
  const name = typeof err?.name === 'string' ? err.name : '';
  const message =
    typeof err?.message === 'string' ? err.message : error instanceof Error ? error.message : String(error);
  return (
    name === 'ChunkLoadError' ||
    /dynamically imported module|importing a module script failed|loading chunk/i.test(message)
  );
}

function hasAlreadyReloaded(): boolean {
  try {
    return sessionStorage.getItem(RELOAD_GUARD_KEY) === '1';
  } catch {
    // sessionStorage unavailable (private mode, storage blocked, ...) —
    // treat as not-yet-reloaded; worst case is one extra reload attempt.
    return false;
  }
}

function markReloaded(): void {
  try {
    sessionStorage.setItem(RELOAD_GUARD_KEY, '1');
  } catch {
    // Best-effort guard only — nothing to fall back to.
  }
}

// Review fix: the guard used to be set-once-forever, so only the *first*
// stale-chunk incident in a tab's lifetime ever recovered automatically —
// a second deploy hitting the same still-open tab found the guard already
// set and gave up silently (render() returning null). Clearing it once this
// boundary mounts without an error means "the app is healthy again" rearms
// the guard for the next incident, while a mount that immediately re-catches
// the same error (a persistent break, not a stale tab) leaves the guard set
// and does not reload-loop.
function clearReloadGuard(): void {
  try {
    sessionStorage.removeItem(RELOAD_GUARD_KEY);
  } catch {
    // ignore — same best-effort guard as above.
  }
}

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  isChunkError: boolean;
}

export default class ChunkErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, isChunkError: false };

  static getDerivedStateFromError(error: unknown): State {
    return { hasError: true, isChunkError: isChunkLoadError(error) };
  }

  componentDidMount(): void {
    // A clean mount (nothing thrown during this commit) means whatever
    // broke last time isn't broken now — safe to rearm. AppShell also
    // remounts this boundary per-route (`key={location.pathname}`), so
    // navigating away from a route that render-errors triggers this too
    // (review fix #2's recovery path).
    if (!this.state.hasError) clearReloadGuard();
  }

  componentDidCatch(_error: unknown): void {
    // `isChunkError` was already classified in getDerivedStateFromError.
    if (!this.state.isChunkError) return;
    if (hasAlreadyReloaded()) return;

    markReloaded();
    window.location.reload();
  }

  handleReload = (): void => {
    clearReloadGuard();
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      // Review fix: previously rendered `null` unconditionally — a single
      // non-chunk render error below this boundary (there's no other one in
      // the app) permanently blanked the content area with no way out short
      // of a manual full reload the user was never told to do. Chunk errors
      // still self-heal via componentDidCatch's automatic reload above; this
      // fallback covers the moment before that lands, and is the *only*
      // recovery for a non-chunk error (its "Try again" button reloads too,
      // since there's no route-local retry available from here).
      return (
        <ErrorState
          title="Something went wrong"
          detail={
            this.state.isChunkError
              ? 'A new version of Papyrus is available.'
              : 'This page ran into a problem.'
          }
          onRetry={this.handleReload}
        />
      );
    }
    return this.props.children;
  }
}
