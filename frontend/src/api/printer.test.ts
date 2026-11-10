import { describe, it, expect } from 'vitest';
import { listJobs } from './printer';

describe('listJobs', () => {
  it('resolves the msw-mocked /api/v1/jobs response', async () => {
    await expect(listJobs()).resolves.toEqual({ items: [], total: 0, limit: 50, offset: 0 });
  });
});
