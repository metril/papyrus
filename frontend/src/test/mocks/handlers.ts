import { http, HttpResponse, type RequestHandler } from 'msw';

export const handlers: RequestHandler[] = [
  http.get('/api/jobs', () => {
    return HttpResponse.json({ jobs: [], total: 0 });
  }),
];
