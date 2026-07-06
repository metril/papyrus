import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FolderOpen } from 'lucide-react';
import EmptyState from './EmptyState';

describe('EmptyState', () => {
  it('renders the title and hint', () => {
    render(<EmptyState title="No files yet" hint="Upload a file to get started." />);
    expect(screen.getByText('No files yet')).toBeInTheDocument();
    expect(screen.getByText('Upload a file to get started.')).toBeInTheDocument();
  });

  it('renders an icon when one is given', () => {
    const { container } = render(<EmptyState icon={FolderOpen} title="No files yet" />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('omits the icon container when no icon is given', () => {
    const { container } = render(<EmptyState title="No files yet" />);
    expect(container.querySelector('svg')).not.toBeInTheDocument();
  });

  it('renders the action node when given', () => {
    render(<EmptyState title="No files yet" action={<button>Upload</button>} />);
    expect(screen.getByRole('button', { name: 'Upload' })).toBeInTheDocument();
  });
});
