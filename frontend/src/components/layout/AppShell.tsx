import { Outlet, NavLink } from 'react-router-dom';
import {
  PrinterIcon,
  DocumentScannerIcon,
  CopyIcon,
  FolderIcon,
  ClockIcon,
  SettingsIcon,
} from '../common/Icons';

const navItems = [
  { to: '/print', label: 'Print', icon: PrinterIcon },
  { to: '/scan', label: 'Scan', icon: DocumentScannerIcon },
  { to: '/copy', label: 'Copy', icon: CopyIcon },
  { to: '/files', label: 'Files', icon: FolderIcon },
  { to: '/history', label: 'History', icon: ClockIcon },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
];

export default function AppShell() {
  return (
    <div className="min-h-screen bg-gray-50 flex">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-64 md:flex-col md:fixed md:inset-y-0 bg-white border-r border-gray-200">
        <div className="flex items-center h-16 px-6 border-b border-gray-200">
          <h1 className="text-xl font-bold text-gray-900">Papyrus</h1>
        </div>
        <nav className="flex-1 px-4 py-4 space-y-1">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-blue-50 text-blue-700'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                }`
              }
            >
              <Icon className="w-5 h-5" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <main className="md:ml-64 flex-1 flex flex-col min-h-screen">
        <div className="flex-1 p-4 md:p-8 pb-20 md:pb-8">
          <Outlet />
        </div>
      </main>

      {/* Mobile bottom navigation */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 bg-white border-t border-gray-200 flex justify-around py-2 z-50">
        {navItems.slice(0, 4).map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex flex-col items-center gap-1 px-3 py-1 text-xs font-medium ${
                isActive ? 'text-blue-600' : 'text-gray-500'
              }`
            }
          >
            <Icon className="w-5 h-5" />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
