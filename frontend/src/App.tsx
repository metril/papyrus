import { lazy, Suspense, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import AppShell from './components/layout/AppShell';
import { ToastProvider } from './components/common/Toast';
import { queryClient } from './api/queryClient';
import { useThemeStore } from './store/themeStore';

// Route-level code splitting: each page becomes its own chunk, loaded on
// first navigation. The Suspense boundary lives in AppShell around <Outlet/>.
const PrintPage = lazy(() => import('./pages/PrintPage'));
const ScanPage = lazy(() => import('./pages/ScanPage'));
const CopyPage = lazy(() => import('./pages/CopyPage'));
const FilesPage = lazy(() => import('./pages/FilesPage'));
const HistoryPage = lazy(() => import('./pages/HistoryPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const AuditPage = lazy(() => import('./pages/AuditPage'));
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));

// Dev-only: lazily loaded and gated on import.meta.env.DEV so it is dead-code
// eliminated from the production bundle.
const ReactQueryDevtools = import.meta.env.DEV
  ? lazy(() =>
      import('@tanstack/react-query-devtools').then((m) => ({
        default: m.ReactQueryDevtools,
      })),
    )
  : null;

export default function App() {
  const { theme } = useThemeStore();

  useEffect(() => {
    const apply = (dark: boolean) => document.documentElement.classList.toggle('dark', dark);
    if (theme === 'system') {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      apply(mq.matches);
      const listener = (e: MediaQueryListEvent) => apply(e.matches);
      mq.addEventListener('change', listener);
      return () => mq.removeEventListener('change', listener);
    }
    apply(theme === 'dark');
  }, [theme]);

  return (
    <ToastProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<Navigate to="/print" replace />} />
              <Route path="/print" element={<PrintPage />} />
              <Route path="/scan" element={<ScanPage />} />
              <Route path="/copy" element={<CopyPage />} />
              <Route path="/files" element={<FilesPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/users" element={<UsersPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
        {ReactQueryDevtools && (
          <Suspense fallback={null}>
            <ReactQueryDevtools initialIsOpen={false} />
          </Suspense>
        )}
      </QueryClientProvider>
    </ToastProvider>
  );
}
