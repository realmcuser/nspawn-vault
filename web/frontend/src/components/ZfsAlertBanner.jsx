import React from 'react';
import { AlertOctagon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// Same deliberately-loud styling as StaleAlertBanner - a broken zfs module
// affects every host/container on this vault at once (every pull, prune,
// and dataset read fails), so it must not read as a minor/quiet warning.
const ZfsAlertBanner = ({ zfsStatus }) => {
  const { t } = useTranslation();

  if (!zfsStatus || zfsStatus.ok) return null;

  const bodyKey = !zfsStatus.loaded ? 'alert.zfsNotLoaded' : 'alert.zfsNotBuilt';

  return (
    <div className="mb-8 bg-red-600 border-2 border-red-400 rounded-xl p-6 shadow-2xl shadow-red-900/50 flex items-start gap-4">
      <AlertOctagon className="w-8 h-8 text-white shrink-0 mt-0.5" />
      <div className="flex-1">
        <h2 className="text-white text-lg font-bold tracking-tight">
          {t('alert.zfsTitle')}
        </h2>
        <p className="text-red-50 text-sm mt-1">
          {t(bodyKey, { kernel: zfsStatus.running_kernel })}
        </p>
      </div>
    </div>
  );
};

export default ZfsAlertBanner;
