import { Outlet, NavLink } from 'react-router-dom';
import { useThemeStore } from '../../store/themeStore';
import {
  PrinterIcon,
  DocumentScannerIcon,
  CopyIcon,
  FolderIcon,
  ClockIcon,
  ChartIcon,
  ShieldIcon,
  SettingsIcon,
} from '../common/Icons';

const navItems = [
  { to: '/print', label: 'Print', icon: PrinterIcon },
  { to: '/scan', label: 'Scan', icon: DocumentScannerIcon },
  { to: '/copy', label: 'Copy', icon: CopyIcon },
  { to: '/files', label: 'Files', icon: FolderIcon },
  { to: '/history', label: 'History', icon: ClockIcon },
  { to: '/dashboard', label: 'Dashboard', icon: ChartIcon },
  { to: '/audit', label: 'Audit', icon: ShieldIcon },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
];

function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, setTheme } = useThemeStore();
  const next = theme === 'light' ? 'dark' : theme === 'dark' ? 'system' : 'light';
  const labels: Record<string, string> = { light: 'Light', dark: 'Dark', system: 'System' };

  const SunIcon = () => (
    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707M17.657 17.657l-.707-.707M6.343 6.343l-.707-.707M12 8a4 4 0 100 8 4 4 0 000-8z" />
    </svg>
  );
  const MoonIcon = () => (
    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
    </svg>
  );
  const SystemIcon = () => (
    <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
    </svg>
  );
  const icons: Record<string, React.ReactNode> = {
    light: <SunIcon />,
    dark: <MoonIcon />,
    system: <SystemIcon />,
  };

  if (compact) {
    return (
      <button
        onClick={() => setTheme(next)}
        title={`Theme: ${labels[theme]}`}
        className="flex items-center gap-1 px-2 py-1 text-xs font-medium text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
      >
        {icons[theme]}
      </button>
    );
  }

  return (
    <button
      onClick={() => setTheme(next)}
      title={`Theme: ${labels[theme]} — click to switch to ${labels[next]}`}
      className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800 transition-colors"
    >
      {icons[theme]}
      <span>{labels[theme]}</span>
    </button>
  );
}

export default function AppShell() {
  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-64 md:flex-col md:fixed md:inset-y-0 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700">
        <div className="flex items-center h-16 px-6 border-b border-gray-200 dark:border-gray-700">
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Papyrus</h1>
        </div>
        <nav className="flex-1 px-4 py-4 space-y-1">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100'
                }`
              }
            >
              <Icon className="w-5 h-5" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 pb-4 pt-2 border-t border-gray-200 dark:border-gray-700">
          <ThemeToggle />
        </div>
      </aside>

      {/* Main content */}
      <main className="md:ml-64 flex-1 flex flex-col min-h-screen">
        <div className="flex-1 p-4 md:p-8 pb-20 md:pb-8">
          <Outlet />
        </div>
      </main>

      {/* Mobile bottom navigation */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 flex justify-around py-2 z-50">
        {navItems.slice(0, 3).map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex flex-col items-center gap-1 px-3 py-1 text-xs font-medium ${
                isActive ? 'text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400'
              }`
            }
          >
            <Icon className="w-5 h-5" />
            {label}
          </NavLink>
        ))}
        <NavLink
          to="/settings"
          className={({ isActive }) =>
            `flex flex-col items-center gap-1 px-3 py-1 text-xs font-medium ${
              isActive ? 'text-blue-600 dark:text-blue-400' : 'text-gray-500 dark:text-gray-400'
            }`
          }
        >
          <SettingsIcon className="w-5 h-5" />
          Settings
        </NavLink>
        <ThemeToggle compact />
      </nav>
    </div>
  );
}
