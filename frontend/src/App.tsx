import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/layout/AppShell';
import PrintPage from './pages/PrintPage';
import ScanPage from './pages/ScanPage';
import CopyPage from './pages/CopyPage';
import FilesPage from './pages/FilesPage';
import HistoryPage from './pages/HistoryPage';
import SettingsPage from './pages/SettingsPage';

export default function App() {
  return (
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
  );
}
