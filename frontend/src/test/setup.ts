import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import { server } from './mocks/server';

// @testing-library/react's asyncWrapper (used internally by userEvent) only
// drains pending microtasks through a fake clock when it detects Jest's fake
// timers via a global `jest` object. Vitest's fake timers use the same
// underlying clock but don't expose that global, so without this shim any
// userEvent interaction while `vi.useFakeTimers()` is active hangs forever
// waiting on a real setTimeout that a fake clock never fires.
// See https://github.com/testing-library/react-testing-library/issues/1197
(globalThis as unknown as { jest?: { advanceTimersByTime: typeof vi.advanceTimersByTime } }).jest = {
  advanceTimersByTime: (ms) => vi.advanceTimersByTime(ms),
};

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

afterEach(() => {
  server.resetHandlers();
  cleanup();
});

afterAll(() => server.close());
