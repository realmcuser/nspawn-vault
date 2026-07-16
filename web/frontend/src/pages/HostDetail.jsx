import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowLeft, Archive, Database, Play, Loader2, Check, Download, FolderOpen } from 'lucide-react';
import {
  fetchHostDetail, fetchContainerLog, triggerHostPull,
  fetchContainerSnapshots, buildContainerDownloadUrl,
} from '../services/api';
import { useAuth } from '../context/AuthContext';
import StatusBadge from '../components/StatusBadge';
import Spinner from '../components/Spinner';
import Modal from '../components/Modal';
import SnapshotBrowser from '../components/SnapshotBrowser';
import { formatBytes, formatTimestamp, formatEpoch } from '../utils/format';

const HostDetail = () => {
  const { host } = useParams();
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [logModal, setLogModal] = useState(null); // { container, loading, error, data }
  const [triggering, setTriggering] = useState(false);
  const [triggerResult, setTriggerResult] = useState(null);
  const [downloadModal, setDownloadModal] = useState(null); // { container, loading, error, snapshots, snapshot, compression }
  const [browseContainer, setBrowseContainer] = useState(null);

  useEffect(() => {
    fetchHostDetail(host)
      .then(setDetail)
      .catch((err) => setError(err.message || 'Failed to load host detail'));
  }, [host]);

  // While a pull is actively running, keep polling so the "Running" badge
  // and container statuses update on their own once it finishes - matches
  // the fact that a first full pull can take anywhere from seconds to over
  // an hour, so a one-time load isn't enough to see it through.
  useEffect(() => {
    if (!detail?.pull_running) return undefined;
    const interval = setInterval(() => {
      fetchHostDetail(host).then(setDetail).catch(() => {});
    }, 15_000);
    return () => clearInterval(interval);
  }, [host, detail?.pull_running]);

  const openLog = async (container) => {
    setLogModal({ container, loading: true, error: null, data: null });
    try {
      const data = await fetchContainerLog(host, container);
      setLogModal({ container, loading: false, error: null, data });
    } catch (err) {
      setLogModal({ container, loading: false, error: err.message, data: null });
    }
  };

  const openDownload = async (container) => {
    setDownloadModal({ container, loading: true, error: null, snapshots: [], snapshot: '', compression: 'zstd' });
    try {
      const snapshots = await fetchContainerSnapshots(host, container);
      setDownloadModal({
        container,
        loading: false,
        error: null,
        snapshots,
        snapshot: snapshots[0]?.name || '',
        compression: 'zstd',
      });
    } catch (err) {
      setDownloadModal({ container, loading: false, error: err.message, snapshots: [], snapshot: '', compression: 'zstd' });
    }
  };

  const handleTriggerPull = async () => {
    setTriggering(true);
    setTriggerResult(null);
    try {
      await triggerHostPull(host);
      setTriggerResult({ success: true, message: t('host.pullStarted') });
    } catch (err) {
      setTriggerResult({ success: false, message: err.message });
    } finally {
      setTriggering(false);
    }
  };

  return (
    <div>
      <Link to="/" className="inline-flex items-center gap-2 text-text-muted hover:text-primary text-sm mb-4 transition-colors">
        <ArrowLeft className="w-4 h-4" /> {t('host.back')}
      </Link>

      <div className="flex items-start justify-between gap-4 flex-wrap mb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white font-mono">{host}</h1>
            {detail?.pull_running && <StatusBadge status="running" />}
          </div>
          {detail && (
            <p className="text-text-muted mt-1">
              {t('host.nextPull')}: {formatEpoch(detail.next_pull_epoch)}
            </p>
          )}
          {triggerResult && (
            <div className={`mt-3 p-3 rounded-lg text-sm flex items-center gap-2 ${triggerResult.success ? 'bg-green-500/10 border border-green-500/20 text-green-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'}`}>
              {triggerResult.success ? <Check className="w-4 h-4 shrink-0" /> : null}
              {triggerResult.message}
            </div>
          )}
        </div>
        {isAdmin && detail && (
          <button
            onClick={handleTriggerPull}
            disabled={triggering}
            className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
          >
            {triggering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {t('host.runNow')}
          </button>
        )}
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {!detail && !error ? (
        <div className="flex items-center justify-center h-64">
          <Spinner className="w-8 h-8" />
        </div>
      ) : detail && detail.containers.length === 0 ? (
        <div className="text-center py-12 bg-surface border border-border rounded-xl">
          <Archive className="w-12 h-12 text-text-muted mx-auto mb-4 opacity-50" />
          <h3 className="text-lg font-medium text-white">{t('host.noContainersTitle')}</h3>
        </div>
      ) : detail && (
        <div className="border border-border rounded-lg overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-hover text-text-muted border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium">{t('host.colContainer')}</th>
                <th className="px-4 py-3 font-medium">{t('host.colLastPull')}</th>
                <th className="px-4 py-3 font-medium">{t('host.colStatus')}</th>
                <th className="px-4 py-3 font-medium">{t('host.colSnapshot')}</th>
                <th className="px-4 py-3 font-medium">{t('host.colRetention')}</th>
                <th className="px-4 py-3 font-medium">{t('host.colSize')}</th>
                {isAdmin && <th className="px-4 py-3 font-medium text-right">{t('admin.actions')}</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {detail.containers.map((c) => (
                <tr key={c.name} className="hover:bg-surface-hover transition-colors">
                  <td className="px-4 py-3 font-mono">
                    {c.name}
                    {c.db_backed_up && (
                      <span
                        className="ml-2 inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 text-xs font-sans align-middle"
                        title={t('host.dbBackedUpHint')}
                      >
                        <Database className="w-3 h-3" /> DB
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">{formatTimestamp(c.last_pull_ts)}</td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => openLog(c.name)}
                      className="hover:opacity-80 transition-opacity"
                      title={t('host.viewLog')}
                    >
                      <StatusBadge status={detail.pull_running ? 'running' : c.status} />
                    </button>
                    {!detail.pull_running && c.status === 'failed' && c.last_pull_msg && (
                      <p className="text-xs text-red-400/80 mt-1">{c.last_pull_msg}</p>
                    )}
                    {!detail.pull_running && c.ransomware_suspected && (
                      <p className="text-xs text-red-400/80 mt-1">
                        {t('host.ransomwareHint', { count: c.changed_entries })}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-text-muted text-xs">{c.last_snapshot || '—'}</td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {c.retention ? (
                      <span
                        title={t('host.retentionHint', {
                          current: c.retention.current_count,
                          retained: c.retention.retained_count,
                        })}
                      >
                        {c.retention.current_count}
                        {c.retention.prunable_count > 0 && (
                          <span className="text-text-muted"> / {c.retention.retained_count}</span>
                        )}
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="px-4 py-3">{formatBytes(c.used_bytes)}</td>
                  {isAdmin && (
                    <td className="px-4 py-3 text-right whitespace-nowrap">
                      <button
                        onClick={() => setBrowseContainer(c.name)}
                        className="p-1.5 text-text-muted hover:text-primary hover:bg-primary/10 rounded-lg transition-colors"
                        title={t('host.browseTitle', { container: c.name })}
                      >
                        <FolderOpen className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => openDownload(c.name)}
                        className="p-1.5 text-text-muted hover:text-primary hover:bg-primary/10 rounded-lg transition-colors"
                        title={t('host.download')}
                      >
                        <Download className="w-4 h-4" />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {logModal && (
        <Modal title={t('host.logTitle', { container: logModal.container })} onClose={() => setLogModal(null)} wide>
          {logModal.loading ? (
            <div className="flex items-center justify-center py-8">
              <Spinner className="w-6 h-6" />
            </div>
          ) : logModal.error ? (
            <p className="text-red-400 text-sm">{logModal.error}</p>
          ) : logModal.data.log ? (
            <>
              <p className="text-xs text-text-muted mb-2">
                {t('host.logLastResult')}: {formatTimestamp(logModal.data.ts)}
              </p>
              <pre className="text-xs font-mono text-text-muted whitespace-pre-wrap bg-background rounded-lg p-4 overflow-auto max-h-[60vh]">
                {logModal.data.log}
              </pre>
            </>
          ) : (
            <p className="text-text-muted text-sm">{t('host.noLogAvailable')}</p>
          )}
        </Modal>
      )}

      {downloadModal && (
        <Modal title={t('host.downloadTitle', { container: downloadModal.container })} onClose={() => setDownloadModal(null)}>
          {downloadModal.loading ? (
            <div className="flex items-center justify-center py-8">
              <Spinner className="w-6 h-6" />
            </div>
          ) : downloadModal.error ? (
            <p className="text-red-400 text-sm">{downloadModal.error}</p>
          ) : downloadModal.snapshots.length === 0 ? (
            <p className="text-text-muted text-sm">{t('host.noSnapshotsAvailable')}</p>
          ) : (
            <div className="space-y-4">
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('host.snapshotLabel')}</label>
                <select
                  value={downloadModal.snapshot}
                  onChange={(e) => setDownloadModal((p) => ({ ...p, snapshot: e.target.value }))}
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm font-mono focus:outline-none focus:border-primary"
                >
                  {downloadModal.snapshots.map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.name} ({formatEpoch(s.creation_epoch)})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('host.compressionLabel')}</label>
                <select
                  value={downloadModal.compression}
                  onChange={(e) => setDownloadModal((p) => ({ ...p, compression: e.target.value }))}
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
                >
                  <option value="zstd">zstd ({t('host.compressionRecommended')})</option>
                  <option value="gzip">gzip (.tar.gz)</option>
                  <option value="none">{t('host.compressionNone')}</option>
                </select>
              </div>
              <a
                href={buildContainerDownloadUrl(host, downloadModal.container, {
                  snapshot: downloadModal.snapshot,
                  compression: downloadModal.compression,
                })}
                className="flex items-center justify-center gap-2 w-full px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg text-sm font-medium transition-colors"
              >
                <Download className="w-4 h-4" />
                {t('host.download')}
              </a>
            </div>
          )}
        </Modal>
      )}

      {browseContainer && (
        <SnapshotBrowser host={host} container={browseContainer} onClose={() => setBrowseContainer(null)} />
      )}
    </div>
  );
};

export default HostDetail;
