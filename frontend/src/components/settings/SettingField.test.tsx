import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SettingField } from './shared';

describe('SettingField checkbox (F19)', () => {
  it.each([
    ['false' as const, false],
    [false as const, false],
    ['true' as const, true],
    [true as const, true],
  ])('renders %p as aria-checked=%s', (value, expected) => {
    render(<SettingField label="Require release PIN" value={value} onChange={() => {}} type="checkbox" />);

    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', String(expected));
  });

  it('clicking an on-then-off draft value ("false" string) actually renders off, not stuck on', () => {
    // Reproduces the exact bug: after a first toggle, SettingsPage's `set`
    // stores the stringified value in the draft — 'false', not the boolean
    // `false`. The old `Boolean(value)` read that as truthy and snapped the
    // switch back on.
    const { rerender } = render(
      <SettingField label="Require release PIN" value={true} onChange={() => {}} type="checkbox" />
    );
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');

    rerender(<SettingField label="Require release PIN" value="false" onChange={() => {}} type="checkbox" />);
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  });

  it('onChange still emits the stringified boolean on click', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SettingField label="Require release PIN" value="false" onChange={onChange} type="checkbox" />);

    await user.click(screen.getByRole('switch'));

    expect(onChange).toHaveBeenCalledWith('true');
  });
});

describe('SettingField text input', () => {
  it('renders the given value and forwards typed input via onChange', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SettingField label="Base URL" value="https://old.example.com" onChange={onChange} />);

    const input = screen.getByDisplayValue('https://old.example.com');
    await user.type(input, 'x');

    expect(onChange).toHaveBeenCalledWith('https://old.example.comx');
  });
});
