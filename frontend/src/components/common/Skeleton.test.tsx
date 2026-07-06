import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import Skeleton from './Skeleton';

describe('Skeleton', () => {
  it.each(['text', 'row', 'card', 'thumbnail'] as const)('renders the %s variant as a hidden placeholder block', (variant) => {
    const { container } = render(<Skeleton variant={variant} />);
    const block = container.querySelector('[aria-hidden="true"]');
    expect(block).toBeInTheDocument();
  });

  it('renders one block by default', () => {
    const { container } = render(<Skeleton />);
    expect(container.querySelectorAll('[aria-hidden="true"]')).toHaveLength(1);
  });

  it('renders `count` repeated blocks', () => {
    const { container } = render(<Skeleton variant="row" count={3} />);
    expect(container.querySelectorAll('[aria-hidden="true"]')).toHaveLength(3);
  });

  it('passes through a custom className to each block', () => {
    const { container } = render(<Skeleton className="my-custom-class" count={2} />);
    expect(container.querySelectorAll('.my-custom-class')).toHaveLength(2);
  });
});
