import { describe, it, expect } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from './test/mocks/server';
import App from './App';

describe('/contacts route', () => {
  it('replaces itself with the Address book settings tab', async () => {
    const user = { id: '2', email: 'u@x.io', display_name: 'U', role: 'user' };
    server.use(
      http.get('/api/v1/auth/me', () => HttpResponse.json(user)),
      http.get('/api/v1/contacts', () => HttpResponse.json([])),
      http.all('/api/v1/*', () => HttpResponse.json([])),
    );
    window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
    window.history.pushState({}, '', '/contacts');
    const before = window.history.length;
    render(<App />);
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe('/settings?tab=contacts'));
    expect(window.history.length).toBe(before);
  });
});
