import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// Deliberately calm, not an alert - a pull actively in progress is good
// news (the system is working), not something to escalate visually. Sits
// alongside StaleAlertBanner/ZfsAlertBanner but uses the app's own primary
// color instead of red, and a plain border instead of a solid fill.
const PullRunningBanner = ({ runningHosts = [] }) => {
  const navigate = useNavigate();
  const { t } = useTranslation();

  if (runningHosts.length === 0) return null;

  return (
    <div className="mb-6 bg-primary/10 border border-primary/30 rounded-xl p-4 flex items-start gap-3">
      <Loader2 className="w-5 h-5 text-primary shrink-0 mt-0.5 animate-spin" />
      <div className="flex-1">
        <p className="text-text text-sm font-medium">
          {t('alert.pullRunningBody', { count: runningHosts.length })}
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {runningHosts.map((h) => (
            <button
              key={h}
              onClick={() => navigate(`/hosts/${encodeURIComponent(h)}`)}
              className="px-3 py-1 bg-surface hover:bg-surface-hover text-text rounded-lg text-xs font-medium transition-colors border border-border"
            >
              {h}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};

export default PullRunningBanner;
