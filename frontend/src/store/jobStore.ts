import { create } from 'zustand';
import type { PrintJob } from '../types';
import api from '../api/client';

interface JobState {
  jobs: PrintJob[];
  loading: boolean;
  error: string | null;
  fetchJobs: () => Promise<void>;
  updateJob: (job: PrintJob) => void;
  releaseJob: (id: number, pin?: string) => Promise<void>;
  cancelJob: (id: number) => Promise<void>;
  deleteJob: (id: number) => Promise<void>;
  reprintJob: (id: number) => Promise<void>;
}

export const useJobStore = create<JobState>((set, get) => ({
  jobs: [],
  loading: false,
  error: null,

  fetchJobs: async () => {
    try {
      set({ loading: true, error: null });
      const response = await api.get('/jobs');
      set({ jobs: response.data.jobs, loading: false });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to fetch jobs';
      set({ error: message, loading: false });
    }
  },

  updateJob: (updatedJob: PrintJob) => {
    set({
      jobs: get().jobs.map((j) => (j.id === updatedJob.id ? updatedJob : j)),
    });
  },

  releaseJob: async (id: number, pin?: string) => {
    await api.post(`/jobs/${id}/release`, pin ? { pin } : undefined);
    get().fetchJobs();
  },

  cancelJob: async (id: number) => {
    await api.post(`/jobs/${id}/cancel`);
    get().fetchJobs();
  },

  deleteJob: async (id: number) => {
    await api.delete(`/jobs/${id}`);
    set({ jobs: get().jobs.filter((j) => j.id !== id) });
  },

  reprintJob: async (id: number) => {
    await api.post(`/jobs/${id}/reprint`);
    get().fetchJobs();
  },
}));
