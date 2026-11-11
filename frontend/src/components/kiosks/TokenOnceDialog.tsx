import { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import Button from '../common/Button';

interface Props {
  token: string;
  kioskName: string;
  onClose: () => void;
}

/** Shows a freshly minted kiosk token exactly once, with a copy button. */
export default function TokenOnceDialog({ token, kioskName, onClose }: Props) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
    } catch {
      // Clipboard unavailable: the token is select-all so it can be copied by hand.
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Kiosk token"
        className="w-full max-w-lg rounded-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 shadow-xl p-6 space-y-4"
      >
        <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">Token for {kioskName}</h3>
        <p className="text-sm text-amber-800 dark:text-amber-300">
          Copy this token now &mdash; it won&apos;t be shown again. Set it as PAPYRUS_TOKEN on the kiosk.
        </p>
        <code className="font-mono text-xs break-all block bg-amber-100 dark:bg-amber-900/40 text-amber-900 dark:text-amber-200 p-2 rounded-lg select-all">
          {token}
        </code>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={copy}>
            {copied ? <Check className="w-4 h-4 inline mr-1" /> : <Copy className="w-4 h-4 inline mr-1" />}
            {copied ? 'Copied' : 'Copy'}
          </Button>
          <Button onClick={onClose}>Done</Button>
        </div>
      </div>
    </div>
  );
}
