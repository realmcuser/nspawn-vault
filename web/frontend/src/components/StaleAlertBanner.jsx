import React from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// Deliberately escalated past every red usage elsewhere in this app (and
// past everything in the visual vocabulary of the internal admin tool this
// project otherwise borrows from): solid fill, larger icon, brighter/thicker border, colored
// shadow. This is the one place that must not look like an ordinary error
// banner — see cockpit-nspawn-vault-design.md for why.
const StaleAlertBanner = ({ staleHosts = [], failedHosts = [] }) => {
  const navigate = useNavigate();
  const { t } = useTranslation();

  const hosts = [...failedHosts, ...staleHosts];
  if (hosts.length === 0) return null;

  return (
    <div className="mb-8 bg-red-600 border-2 border-red-400 rounded-xl p-6 shadow-2xl shadow-red-900/50 flex items-start gap-4">
      <AlertTriangle className="w-8 h-8 text-white shrink-0 mt-0.5" />
      <div className="flex-1">
        <h2 className="text-white text-lg font-bold tracking-tight">
          {t('alert.staleTitle')}
        </h2>
        <p className="text-red-50 text-sm mt-1">
          {t('alert.staleBody', { count: hosts.length })}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {hosts.map((h) => (
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

export default StaleAlertBanner;
