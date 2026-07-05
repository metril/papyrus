import Card from '../common/Card';
import Toggle from '../common/Toggle';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function OcrCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card title="OCR / Searchable PDFs" collapsible>
      <div className="space-y-3">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          Apply Tesseract OCR to scanned PDFs to make them searchable. Requires tesseract-ocr and ocrmypdf.
        </p>
        <Toggle
          checked={appSettings.ocr_enabled === true || appSettings.ocr_enabled === 'true'}
          onChange={(v) => set('ocr_enabled')(String(v))}
          label="Enable OCR for auto-deliver scans"
        />
        <SettingField
          label="OCR Language"
          value={appSettings.ocr_language ?? 'eng'}
          onChange={set('ocr_language')}
          placeholder="eng"
        />
        <div className="flex justify-end">
          <SaveButton section="ocr" keys={['ocr_enabled', 'ocr_language']} save={save} />
        </div>
      </div>
    </Card>
  );
}
