import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/layout/AppShell';
import { ToastProvider } from './components/common/Toast';
import PrintPage from './pages/PrintPage';
import ScanPage from './pages/ScanPage';
import CopyPage from './pages/CopyPage';
import FilesPage from './pages/FilesPage';
import HistoryPage from './pages/HistoryPage';
import SettingsPage from './pages/SettingsPage';
import { useThemeStore } from './store/themeStore';

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
        </Route>
      </Routes>
    </BrowserRouter>
    </ToastProvider>
  );
}
