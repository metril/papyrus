import { Component, type ReactNode } from 'react';

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
  const message = error instanceof Error ? error.message : String(error);
  return /dynamically imported module|importing a module script failed|loading chunk/i.test(message);
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

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

export default class ChunkErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: unknown): void {
    if (!isChunkLoadError(error)) return;
    if (hasAlreadyReloaded()) return;

    markReloaded();
    window.location.reload();
  }

  render(): ReactNode {
    if (this.state.hasError) {
      // Either the reload above is in flight, or this was some other render
      // error below this boundary — render nothing rather than leave a
      // broken subtree mounted (this is the only error boundary in the app).
      return null;
    }
    return this.props.children;
  }
}
