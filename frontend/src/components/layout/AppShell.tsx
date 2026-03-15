import { useEffect, useState } from 'react';
import { Outlet, NavLink } from 'react-router-dom';
import api from '../../api/client';
import { useThemeStore } from '../../store/themeStore';
import { useAuthStore } from '../../store/authStore';

const UsersIcon = ({ className }: { className?: string }) => (
  <svg xmlns="http://www.w3.org/2000/svg" className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197m13.5-9a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0z" />
  </svg>
);

const LogoutIcon = ({ className }: { className?: string }) => (
  <svg xmlns="http://www.w3.org/2000/svg" className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
  </svg>
);
import { useWebSocket } from '../../hooks/useWebSocket';
import { useToast } from '../common/Toast';
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
  { to: '/dashboard', label: 'Dashboard', icon: ChartIcon, adminOnly: true },
  { to: '/users', label: 'Users', icon: UsersIcon, adminOnly: true },
  { to: '/audit', label: 'Audit', icon: ShieldIcon, adminOnly: true },
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

function LoginScreen() {
  const [providers, setProviders] = useState<{ local_enabled: boolean; oidc_enabled: boolean; admin_override: boolean } | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loggingIn, setLoggingIn] = useState(false);
  const { fetchUser } = useAuthStore();

  useEffect(() => {
    api.get('/auth/providers').then(({ data }) => setProviders(data)).catch(() => setProviders({ local_enabled: true, oidc_enabled: false, admin_override: false }));
  }, []);

  const handleLocalLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoggingIn(true);
    try {
      await api.post('/auth/local-login', { username, password });
      await fetchUser();
    } catch (err) {
      setError('Invalid username or password');
    } finally {
      setLoggingIn(false);
    }
  };

  if (!providers) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-gray-200 border-t-blue-500 rounded-full animate-spinner dark:border-gray-700 dark:border-t-blue-400" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center">
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-xl shadow-gray-300/30 dark:shadow-black/30 border border-gray-100 dark:border-gray-800 p-8 w-full max-w-sm space-y-6">
        <div className="text-center">
          <div className="mx-auto w-12 h-12 bg-blue-50 dark:bg-blue-950/50 rounded-xl flex items-center justify-center mb-3">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-6 h-6 text-blue-600 dark:text-blue-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M6.72 13.829c-.24.03-.48.062-.72.096m.72-.096a42.415 42.415 0 0110.56 0m-10.56 0L6.34 18m10.94-4.171c.24.03.48.062.72.096m-.72-.096L17.66 18m0 0l.229 2.523a1.125 1.125 0 01-1.12 1.227H7.231c-.662 0-1.18-.568-1.12-1.227L6.34 18m11.318 0h1.091A2.25 2.25 0 0021 15.75V9.456c0-1.081-.768-2.015-1.837-2.175a48.055 48.055 0 00-1.913-.247M6.34 18H5.25A2.25 2.25 0 013 15.75V9.456c0-1.081.768-2.015 1.837-2.175a48.041 48.041 0 011.913-.247m10.5 0a48.536 48.536 0 00-10.5 0m10.5 0V3.375c0-.621-.504-1.125-1.125-1.125h-8.25c-.621 0-1.125.504-1.125 1.125v3.659M18 10.5h.008v.008H18V10.5zm-3 0h.008v.008H15V10.5z" /></svg>
          </div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 tracking-tight">Papyrus</h1>
          <p className="text-sm text-gray-400 dark:text-gray-500 mt-1">Print & Scan Server</p>
        </div>

        {(providers.local_enabled || providers.admin_override) && (
          <form onSubmit={handleLocalLogin} className="space-y-3">
            <input
              type="text"
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2.5 bg-white dark:bg-gray-800 dark:text-gray-100"
              autoFocus
            />
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2.5 bg-white dark:bg-gray-800 dark:text-gray-100"
            />
            {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
            <button
              type="submit"
              disabled={loggingIn || !username || !password}
              className="w-full px-4 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-medium rounded-lg transition-all duration-150 shadow-sm shadow-blue-600/25 hover:shadow-md active:scale-[0.98] dark:bg-blue-500 dark:hover:bg-blue-400"
            >
              {loggingIn ? 'Signing in...' : 'Sign in'}
            </button>
          </form>
        )}

        {(providers.local_enabled || providers.admin_override) && providers.oidc_enabled && (
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-gray-200 dark:bg-gray-700" />
            <span className="text-xs text-gray-400">or</span>
            <div className="flex-1 h-px bg-gray-200 dark:bg-gray-700" />
          </div>
        )}

        {providers.oidc_enabled && (
          <button
            onClick={() => { window.location.href = '/api/auth/login'; }}
            className="w-full px-4 py-2.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-900 dark:text-gray-100 font-medium rounded-lg transition-colors border border-gray-300 dark:border-gray-600"
          >
            Sign in with SSO
          </button>
        )}

        {!providers.local_enabled && !providers.oidc_enabled && !providers.admin_override && (
          <p className="text-sm text-red-600 dark:text-red-400 text-center">
            No authentication methods configured. Set PAPYRUS_ADMIN_USERNAME and PAPYRUS_ADMIN_PASSWORD environment variables.
          </p>
        )}
      </div>
    </div>
  );
}

export default function AppShell() {
  const toast = useToast();
  const { user, loading, logout, fetchUser } = useAuthStore();
  useEffect(() => { fetchUser(); }, [fetchUser]);
  const isAdmin = user?.role === 'admin';
  const visibleNavItems = navItems.filter((item) => !item.adminOnly || isAdmin);

  useWebSocket({
    url: '/api/system/ws/scans',
    onMessage: (msg) => {
      if (msg.type === 'scan_completed') {
        toast.show('Scan completed', 'success');
      }
    },
  });

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-gray-200 border-t-blue-500 rounded-full animate-spinner dark:border-gray-700 dark:border-t-blue-400" />
      </div>
    );
  }

  if (!user) {
    return <LoginScreen />;
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-64 md:flex-col md:fixed md:inset-y-0 bg-white dark:bg-gray-900 border-r border-gray-100 dark:border-gray-800">
        <div className="flex flex-col justify-center h-16 px-6 border-b border-gray-100 dark:border-gray-800">
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100 tracking-tight">Papyrus</h1>
          <p className="text-xs text-gray-400 dark:text-gray-500 -mt-0.5">Print & Scan</p>
        </div>
        <nav className="flex-1 px-4 py-4 space-y-1">
          {visibleNavItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 ${
                  isActive
                    ? 'bg-blue-50/70 text-blue-700 border-l-2 border-blue-600 dark:bg-blue-950/50 dark:text-blue-300 dark:border-blue-400'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900 border-l-2 border-transparent dark:text-gray-400 dark:hover:bg-gray-800/50 dark:hover:text-gray-100'
                }`
              }
            >
              <Icon className="w-5 h-5" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 pb-4 pt-2 border-t border-gray-100 dark:border-gray-800 space-y-2">
          {user && (
            <div className="flex items-center justify-between">
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{user.display_name}</p>
                <div className="flex items-center gap-1.5">
                  <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${isAdmin ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>
                    {user.role}
                  </span>
                </div>
              </div>
              <button
                onClick={logout}
                title="Sign out"
                className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              >
                <LogoutIcon className="w-4 h-4" />
              </button>
            </div>
          )}
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
      <nav className="md:hidden fixed bottom-0 inset-x-0 bg-white dark:bg-gray-900 border-t border-gray-100 dark:border-gray-800 flex justify-around py-2 z-50">
        {visibleNavItems.slice(0, 3).map(({ to, label, icon: Icon }) => (
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
