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

// Review fix (critical regression): this boundary sits *above* `<Suspense>`,
// and `React.lazy` routes commit the Suspense fallback first — a perfectly
// clean render with nothing thrown — before the rejected dynamic import is
// even known. A mount-time "clear the guard because nothing threw yet"
// (the previous fix) cleared it on *every single load*, so a persistently
// broken chunk (bad deploy, offline network, asset-host outage) reloaded on
// every page load forever: clean Suspense mount → guard cleared →
// rejection → componentDidCatch sees no guard → reload → repeat. A
// time-based guard instead bounds the automatic reload to at most once per
// window, regardless of how many "clean mount, then catch" cycles happen.
const RELOAD_GUARD_TTL_MS = 30_000;

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

/** Reads the guard's timestamp, or `null` if unset/unreadable/unparseable. */
function guardTimestamp(): number | null {
  try {
    const raw = sessionStorage.getItem(RELOAD_GUARD_KEY);
    if (raw === null) return null;
    const ts = Number(raw);
    return Number.isFinite(ts) ? ts : null;
  } catch {
    // sessionStorage unavailable (private mode, storage blocked, ...) —
    // treat as unset; worst case is one extra reload attempt.
    return null;
  }
}

/** True when a guard exists and is younger than the TTL — i.e. an
 * automatic reload already happened recently and must not happen again
 * yet. A missing guard, or one older than the TTL, is NOT fresh. */
function isGuardFresh(): boolean {
  const ts = guardTimestamp();
  return ts !== null && Date.now() - ts < RELOAD_GUARD_TTL_MS;
}

function markReloaded(): void {
  try {
    sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
  } catch {
    // Best-effort guard only — nothing to fall back to.
  }
}

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

  componentDidCatch(_error: unknown): void {
    // `isChunkError` was already classified in getDerivedStateFromError.
    if (!this.state.isChunkError) return;
    // A fresh guard means an automatic reload already happened within the
    // last 30s — this catch is either the same broken chunk on the reload
    // that just happened, or a second Suspense/lazy cycle before that
    // reload has actually navigated away. Either way, don't reload again;
    // render() below shows the fallback with a manual "Reload" button
    // instead, and a *stale* (or absent) guard is free to reload again.
    if (isGuardFresh()) return;

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
      // still self-heal via componentDidCatch's automatic reload above (up
      // to once per 30s); this fallback covers the moment before that
      // lands (or the case where the guard is still fresh and it
      // deliberately didn't auto-reload), and is the only recovery for a
      // non-chunk error.
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
