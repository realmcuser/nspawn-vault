import React from 'react';
import { AlertOctagon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// Same deliberately-loud styling as StaleAlertBanner/ZfsAlertBanner - the
// vault running out of space breaks every pull on it, not just one host's,
// so it must not read as a minor/quiet warning either.
const StorageAlertBanner = ({ storageStatus }) => {
  const { t } = useTranslation();

  if (!storageStatus || storageStatus.ok) return null;

  return (
    <div className="mb-8 bg-red-600 border-2 border-red-400 rounded-xl p-6 shadow-2xl shadow-red-900/50 flex items-start gap-4">
      <AlertOctagon className="w-8 h-8 text-white shrink-0 mt-0.5" />
      <div className="flex-1">
        <h2 className="text-white text-lg font-bold tracking-tight">
          {t('alert.storageTitle')}
        </h2>
        <p className="text-red-50 text-sm mt-1">
          {t('alert.storageBody', { percent: storageStatus.percent_free.toFixed(1) })}
        </p>
      </div>
    </div>
  );
};

export default StorageAlertBanner;
