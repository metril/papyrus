import { useState } from 'react';
import { useLoadedPaper } from '../api/queries';

export const DEFAULT_PAPER = 'A4';

/**
 * Paper selection that starts at the printer's loaded paper (falling back to
 * A4) until the user picks something; an explicit pick is never overwritten
 * when the printer query resolves later.
 */
export function usePaperChoice(allowed: readonly string[]): [string, (media: string) => void] {
  const { data: loaded } = useLoadedPaper();
  const [chosen, setChosen] = useState<string | null>(null);
  const detected = loaded && allowed.includes(loaded) ? loaded : DEFAULT_PAPER;
  return [chosen ?? detected, setChosen];
}
