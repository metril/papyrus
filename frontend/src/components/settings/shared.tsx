import Button from '../common/Button';
import Toggle from '../common/Toggle';

export type AppSettings = Record<string, string | number | boolean>;

export type SaveStatusMap = Record<string, 'saving' | 'saved' | 'error' | undefined>;

/**
 * Bundle of the shared save-state that a settings section needs to render a
 * SaveButton. Threaded from SettingsPage down through each value card.
 */
export interface SaveControls {
  status: SaveStatusMap;
  onSave: (section: string, keys: string[]) => void;
  /** True when settings failed to load; disables saving. */
  disabled: boolean;
}

/** Props shared by every value-settings card (the appSettings/set/SaveButton pattern). */
export interface SettingsSectionProps {
  appSettings: AppSettings;
  set: (key: string) => (value: string) => void;
  save: SaveControls;
}

export function SettingField({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
}: {
  label: string;
  value: string | number | boolean;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
}) {
  if (type === 'checkbox') {
    return (
      <Toggle
        checked={Boolean(value)}
        onChange={(v) => onChange(String(v))}
        label={label}
      />
    );
  }
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{label}</label>
      <input
        type={type}
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-gray-300 dark:border-gray-600 text-sm p-2 bg-white dark:bg-gray-800 dark:text-gray-100"
      />
    </div>
  );
}

export function SaveButton({ section, keys, save }: { section: string; keys: string[]; save: SaveControls }) {
  const status = save.status[section];
  return (
    <Button
      onClick={() => save.onSave(section, keys)}
      disabled={status === 'saving' || save.disabled}
      variant={status === 'error' ? 'danger' : 'primary'}
    >
      {status === 'saving' ? 'Saving…' : status === 'saved' ? 'Saved ✓' : status === 'error' ? 'Error' : 'Save'}
    </Button>
  );
}
