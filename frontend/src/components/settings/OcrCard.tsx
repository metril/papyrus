import Card from '../common/Card';
import Toggle from '../common/Toggle';
import { SettingField, SaveButton, type SettingsSectionProps } from './shared';

export default function OcrCard({ appSettings, set, save }: SettingsSectionProps) {
  return (
    <Card
      title="OCR / Searchable PDFs"
      description="Apply Tesseract OCR to scanned PDFs to make them searchable. Requires tesseract-ocr and ocrmypdf."
      collapsible
    >
      <div className="space-y-3">
        <Toggle
          checked={appSettings.ocr_enabled === true || appSettings.ocr_enabled === 'true'}
          onChange={(v) => set('ocr_enabled')(String(v))}
          label="Enable OCR for auto-deliver scans"
        />
        <SettingField
          label="OCR language"
          value={appSettings.ocr_language ?? 'eng'}
          onChange={set('ocr_language')}
          placeholder="eng"
          mono
        />
        <div className="flex justify-end">
          <SaveButton section="ocr" keys={['ocr_enabled', 'ocr_language']} save={save} />
        </div>
      </div>
    </Card>
  );
}
