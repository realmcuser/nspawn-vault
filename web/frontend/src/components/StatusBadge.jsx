import React from 'react';
import { clsx } from 'clsx';

const STYLES = {
  ok: 'bg-green-500/10 text-green-400',
  stale: 'bg-yellow-500/10 text-yellow-400',
  failed: 'bg-red-500/10 text-red-400',
  unknown: 'bg-slate-500/10 text-slate-400',
  running: 'bg-primary/10 text-primary',
  // Deliberately more alarming than "failed" - a plain red/10 badge would
  // read the same as an ordinary pull failure, which undersells this.
  ransomware: 'bg-red-600 text-white',
};

const LABELS = {
  ok: 'OK',
  stale: 'Stale',
  failed: 'Failed',
  unknown: 'Unknown',
  running: 'Running',
  ransomware: 'Ransomware?',
};

const StatusBadge = ({ status }) => (
  <span className={clsx(
    'px-2.5 py-1 rounded-full text-xs font-medium inline-flex items-center gap-1.5',
    STYLES[status] || STYLES.unknown
  )}>
    {status === 'running' && (
      <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
    )}
    {LABELS[status] || status}
  </span>
);

export default StatusBadge;
