import axios from 'axios';
import { useAuthStore } from '../store/authStore';

const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

// F16: these three exchange a 401 as a normal, expected part of the login
// flow itself (an anonymous visitor probing session state, a wrong-password
// attempt) — LoginScreen is what renders that outcome, so the interceptor
// must never touch their 401s.
const AUTH_ENDPOINTS = ['/auth/me', '/auth/local-login', '/auth/providers'];

function isAuthEndpoint(url?: string): boolean {
  return !!url && AUTH_ENDPOINTS.some((path) => url.includes(path));
}

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !isAuthEndpoint(error.config?.url)) {
      // F16: previously a hard `window.location.href = '/api/auth/login'`
      // here — but that endpoint 503s whenever OIDC isn't the configured
      // auth method (the default), replacing the whole SPA with raw JSON
      // before LoginScreen (which offers local login and/or an OIDC button)
      // ever gets a chance to render. Signing out locally instead always
      // lands the user back on LoginScreen, which already renders the SSO
      // button when OIDC is in fact the only configured method — no
      // automatic full-page navigation is ever required to reach it.
      useAuthStore.getState().signOut();
    }
    return Promise.reject(error);
  }
);

export default api;
