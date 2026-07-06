import { type ButtonHTMLAttributes } from 'react';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost' | 'danger-ghost';
  size?: 'sm' | 'md' | 'lg';
}

const variants = {
  primary: 'bg-ink-600 text-white hover:bg-ink-700 shadow-sm shadow-ink-600/25 hover:shadow-md dark:bg-ink-600 dark:hover:bg-ink-500 focus-visible:ring-ink-500',
  secondary: 'bg-white text-gray-700 border border-gray-200 hover:bg-gray-50 dark:bg-gray-800 dark:text-gray-200 dark:border-gray-700 dark:hover:bg-gray-700 focus-visible:ring-ink-500',
  danger: 'bg-red-600 text-white hover:bg-red-700 shadow-sm shadow-red-600/25 hover:shadow-md dark:bg-red-500 dark:hover:bg-red-400 focus-visible:ring-ink-500',
  ghost: 'text-gray-600 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800 focus-visible:ring-ink-500',
  'danger-ghost': 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40 focus-visible:ring-ink-500',
};

const sizes = {
  sm: 'px-3 py-1.5 text-sm',
  md: 'px-4 py-2 text-sm',
  lg: 'px-6 py-3 text-base',
};

export default function Button({
  variant = 'primary',
  size = 'md',
  className = '',
  disabled,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-lg font-medium
        transition-all duration-150
        focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-gray-900
        active:scale-[0.98]
        disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100
        ${variants[variant]} ${sizes[size]} ${className}`}
      disabled={disabled}
      {...props}
    >
      {children}
    </button>
  );
}
