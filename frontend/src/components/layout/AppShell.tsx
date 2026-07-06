import { Suspense, useEffect, useState } from 'react';
import { Outlet, NavLink } from 'react-router-dom';
import api from '../../api/client';
import { useThemeStore } from '../../store/themeStore';
import { useAuthStore } from '../../store/authStore';
import { useRealtimeBridge } from '../../hooks/useRealtimeBridge';
import {
  Printer,
  ScanLine,
  Copy,
  FolderOpen,
  History,
  ChartColumn,
  Users,
  ShieldCheck,
  Settings,
  LogOut,
  Sun,
  Moon,
  Monitor,
} from 'lucide-react';

// Shared default so every icon in this file gets the same visual weight.
const ICON_STROKE_WIDTH = 1.75;

// Full-viewport centered spinner, shared by the auth-loading state, the
// LoginScreen providers fetch, and the Suspense fallback for lazy routes.
const CenteredSpinner = () => (
  <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center">
    <div className="w-6 h-6 border-2 border-gray-200 border-t-blue-500 rounded-full animate-spinner dark:border-gray-700 dark:border-t-blue-400" />
  </div>
);

const navItems = [
  { to: '/print', label: 'Print', icon: Printer },
  { to: '/scan', label: 'Scan', icon: ScanLine },
  { to: '/copy', label: 'Copy', icon: Copy },
  { to: '/files', label: 'Files', icon: FolderOpen },
  { to: '/history', label: 'History', icon: History },
  { to: '/dashboard', label: 'Dashboard', icon: ChartColumn, adminOnly: true },
  { to: '/users', label: 'Users', icon: Users, adminOnly: true },
  { to: '/audit', label: 'Audit', icon: ShieldCheck, adminOnly: true },
  { to: '/settings', label: 'Settings', icon: Settings },
];

function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, setTheme } = useThemeStore();
  const next = theme === 'light' ? 'dark' : theme === 'dark' ? 'system' : 'light';
  const labels: Record<string, string> = { light: 'Light', dark: 'Dark', system: 'System' };

  const icons: Record<string, React.ReactNode> = {
    light: <Sun className="w-4 h-4" strokeWidth={ICON_STROKE_WIDTH} aria-hidden="true" />,
    dark: <Moon className="w-4 h-4" strokeWidth={ICON_STROKE_WIDTH} aria-hidden="true" />,
    system: <Monitor className="w-4 h-4" strokeWidth={ICON_STROKE_WIDTH} aria-hidden="true" />,
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

// Opens the realtime WS→cache bridge only once the user is authenticated, so
// the login screen never spins up sockets. Renders nothing.
function RealtimeBridge() {
  useRealtimeBridge();
  return null;
}

function LoginScreen() {
  const [providers, setProviders] = useState<{ local_enabled: boolean; oidc_enabled: boolean; admin_override: boolean } | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loggingIn, setLoggingIn] = useState(false);
  const fetchUser = useAuthStore((s) => s.fetchUser);

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
    } catch {
      setError('Invalid username or password');
    } finally {
      setLoggingIn(false);
    }
  };

  if (!providers) {
    return <CenteredSpinner />;
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex items-center justify-center">
      <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-xl shadow-gray-300/30 dark:shadow-black/30 border border-gray-100 dark:border-gray-800 p-8 w-full max-w-sm space-y-6">
        <div className="text-center">
          <div className="mx-auto w-12 h-12 bg-blue-50 dark:bg-blue-950/50 rounded-xl flex items-center justify-center mb-3">
            <Printer className="w-6 h-6 text-blue-600 dark:text-blue-400" strokeWidth={ICON_STROKE_WIDTH} aria-hidden="true" />
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
  // Granular selectors: this component doesn't read `error`, so an
  // auth-fetch failure shouldn't force a re-render here.
  const user = useAuthStore((s) => s.user);
  const loading = useAuthStore((s) => s.loading);
  const logout = useAuthStore((s) => s.logout);
  const fetchUser = useAuthStore((s) => s.fetchUser);
  useEffect(() => { fetchUser(); }, [fetchUser]);
  const isAdmin = user?.role === 'admin';
  const visibleNavItems = navItems.filter((item) => !item.adminOnly || isAdmin);

  if (loading) {
    return <CenteredSpinner />;
  }

  if (!user) {
    return <LoginScreen />;
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex">
      <RealtimeBridge />
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
              <Icon className="w-5 h-5" strokeWidth={ICON_STROKE_WIDTH} aria-hidden="true" />
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
                <LogOut className="w-4 h-4" strokeWidth={ICON_STROKE_WIDTH} aria-hidden="true" />
              </button>
            </div>
          )}
          <ThemeToggle />
        </div>
      </aside>

      {/* Main content */}
      <main className="md:ml-64 flex-1 flex flex-col min-h-screen">
        <div className="flex-1 p-4 md:p-8 pb-20 md:pb-8">
          <Suspense fallback={<CenteredSpinner />}>
            <Outlet />
          </Suspense>
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
            <Icon className="w-5 h-5" strokeWidth={ICON_STROKE_WIDTH} aria-hidden="true" />
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
          <Settings className="w-5 h-5" strokeWidth={ICON_STROKE_WIDTH} aria-hidden="true" />
          Settings
        </NavLink>
        <ThemeToggle compact />
      </nav>
    </div>
  );
}
