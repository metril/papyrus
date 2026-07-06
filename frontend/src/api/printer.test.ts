import { describe, it, expect } from 'vitest';
import { listJobs } from './printer';

describe('listJobs', () => {
  it('resolves the msw-mocked /api/jobs response', async () => {
    await expect(listJobs()).resolves.toEqual({ jobs: [], total: 0 });
  });
});
