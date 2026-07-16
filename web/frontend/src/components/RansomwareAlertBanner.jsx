import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ShieldAlert } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// Same loud treatment as StaleAlertBanner/ZfsAlertBanner (this app's visual
// ceiling for "must not be missed") - rendered first among the banners on
// Dashboard, since a suspected ransomware event outranks every other alert
// this app has. Backed by the same 30s alert poll as the others, so it
// appears on its own for anyone already sitting on the dashboard - no
// manual refresh needed.
const RansomwareAlertBanner = ({ ransomwareHosts = [] }) => {
  const navigate = useNavigate();
  const { t } = useTranslation();

  if (ransomwareHosts.length === 0) return null;

  return (
    <div className="mb-8 bg-red-600 border-2 border-red-400 rounded-xl p-6 shadow-2xl shadow-red-900/50 flex items-start gap-4">
      <ShieldAlert className="w-8 h-8 text-white shrink-0 mt-0.5" />
      <div className="flex-1">
        <h2 className="text-white text-lg font-bold tracking-tight">
          {t('alert.ransomwareTitle')}
        </h2>
        <p className="text-red-50 text-sm mt-1">
          {t('alert.ransomwareBody', { count: ransomwareHosts.length })}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {ransomwareHosts.map((h) => (
            <button
              key={h}
              onClick={() => navigate(`/hosts/${encodeURIComponent(h)}`)}
              className="px-3 py-1.5 bg-white/15 hover:bg-white/25 text-white rounded-lg text-sm font-medium transition-colors border border-white/20"
            >
              {h}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};

export default RansomwareAlertBanner;
