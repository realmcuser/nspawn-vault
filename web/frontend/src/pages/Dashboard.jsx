import React, { useEffect, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { HardDrive, Bell, Cpu, Database } from 'lucide-react';
import { fetchHosts, fetchAlertsSummary, fetchGfsSettings, fetchNotifySettings, fetchVaultStorage } from '../services/api';
import { useAuth } from '../context/AuthContext';
import StatusBadge from '../components/StatusBadge';
import StaleAlertBanner from '../components/StaleAlertBanner';
import ZfsAlertBanner from '../components/ZfsAlertBanner';
import PullRunningBanner from '../components/PullRunningBanner';
import StorageAlertBanner from '../components/StorageAlertBanner';
import Spinner from '../components/Spinner';
import { formatBytes, formatTimestamp, formatEpoch } from '../utils/format';

const ALERT_POLL_MS = 30_000;

const Dashboard = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [hosts, setHosts] = useState(null);
  const [alerts, setAlerts] = useState({ stale_hosts: [], failed_hosts: [], running_hosts: [], zfs_module_status: null, storage_status: null, has_alert: false });
  const [gfs, setGfs] = useState(null);
  const [notify, setNotify] = useState(null);
  const [storage, setStorage] = useState(null);
  const [error, setError] = useState('');

  const loadHosts = async () => {
    try {
      setHosts(await fetchHosts());
    } catch (err) {
      setError(err.message || 'Failed to load hosts');
    }
  };

  const loadAlerts = async () => {
    try {
      setAlerts(await fetchAlertsSummary());
    } catch {
      // non-fatal: banner just won't update this cycle
    }
  };

  useEffect(() => {
    loadHosts();
    loadAlerts();
    fetchGfsSettings().then(setGfs).catch(() => {});
    fetchNotifySettings().then(setNotify).catch(() => {});
    fetchVaultStorage().then(setStorage).catch(() => {});

    const interval = setInterval(loadAlerts, ALERT_POLL_MS);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-white">{t('dashboard.title')}</h1>
      <p className="text-text-muted mt-1 mb-6">{t('dashboard.subtitle')}</p>

      <ZfsAlertBanner zfsStatus={alerts.zfs_module_status} />
      <StaleAlertBanner staleHosts={alerts.stale_hosts} failedHosts={alerts.failed_hosts} />
      <PullRunningBanner runningHosts={alerts.running_hosts} />
      <StorageAlertBanner storageStatus={alerts.storage_status} />

      {error && (
        <div className="mb-6 p-4 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {hosts === null ? (
        <div className="flex items-center justify-center h-64">
          <Spinner className="w-8 h-8" />
        </div>
      ) : hosts.length === 0 ? (
        <div className="text-center py-12 bg-surface border border-border rounded-xl">
          <HardDrive className="w-12 h-12 text-text-muted mx-auto mb-4 opacity-50" />
          <h3 className="text-lg font-medium text-white">{t('dashboard.noHostsTitle')}</h3>
          <p className="text-text-muted max-w-md mx-auto mt-2">{t('dashboard.noHostsBody')}</p>
        </div>
      ) : (
        <div className="border border-border rounded-lg overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-hover text-text-muted border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium">{t('dashboard.colServer')}</th>
                <th className="px-4 py-3 font-medium">{t('dashboard.colContainers')}</th>
                <th className="px-4 py-3 font-medium">{t('dashboard.colLastPull')}</th>
                <th className="px-4 py-3 font-medium">{t('dashboard.colStatus')}</th>
                <th className="px-4 py-3 font-medium">{t('dashboard.colDataset')}</th>
                <th className="px-4 py-3 font-medium">{t('dashboard.colNextPull')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {hosts.map((h) => (
                <tr
                  key={h.host}
                  className="group hover:bg-surface-hover transition-colors cursor-pointer"
                  onClick={() => navigate(`/hosts/${encodeURIComponent(h.host)}`)}
                >
                  <td className="px-4 py-3 font-mono">{h.host}</td>
                  <td className="px-4 py-3">{h.container_count}</td>
                  <td className="px-4 py-3">{formatTimestamp(h.last_pull_ts)}</td>
                  <td className="px-4 py-3"><StatusBadge status={h.pull_running ? 'running' : h.status} /></td>
                  <td className="px-4 py-3 font-mono text-text-muted">
                    {h.pool_dataset} ({formatBytes(h.pool_used_bytes)})
                  </td>
                  <td className="px-4 py-3">{formatEpoch(h.next_pull_epoch)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-surface border border-border rounded-xl p-5">
          <h3 className="text-white font-medium flex items-center gap-2">
            <Database className="w-4 h-4 text-primary" /> {t('dashboard.vaultStorageTitle')}
          </h3>
          {storage ? (
            <>
              <p className="text-text-muted text-sm mt-2">
                {formatBytes(storage.used_bytes)} / {formatBytes(storage.total_bytes)}
              </p>
              <div className="w-full h-1.5 bg-surface-hover rounded-full mt-2 overflow-hidden">
                <div
                  className={`h-full rounded-full ${storage.ok ? 'bg-primary' : 'bg-red-500'}`}
                  style={{ width: `${Math.min(100, (storage.used_bytes / storage.total_bytes) * 100)}%` }}
                />
              </div>
              <p className={`text-xs mt-2 ${storage.ok ? 'text-text-muted' : 'text-red-400 font-medium'}`}>
                {t('dashboard.storageFree', { free: formatBytes(storage.available_bytes) })}
              </p>
            </>
          ) : (
            <Spinner className="w-4 h-4 mt-2" />
          )}
        </div>
        <div className="bg-surface border border-border rounded-xl p-5">
          <h3 className="text-white font-medium flex items-center gap-2">
            <Cpu className="w-4 h-4 text-primary" /> {t('dashboard.zfsModuleTitle')}
          </h3>
          {alerts.zfs_module_status ? (
            <>
              <p className={`text-sm mt-2 font-medium ${alerts.zfs_module_status.ok ? 'text-green-400' : 'text-red-400'}`}>
                {alerts.zfs_module_status.ok ? t('dashboard.zfsOk') : t('dashboard.zfsProblem')}
              </p>
              <p className="text-text-muted text-xs mt-1 font-mono">
                {alerts.zfs_module_status.running_kernel}
              </p>
            </>
          ) : (
            <Spinner className="w-4 h-4 mt-2" />
          )}
        </div>
        <div className="bg-surface border border-border rounded-xl p-5">
          <h3 className="text-white font-medium flex items-center gap-2">
            <HardDrive className="w-4 h-4 text-primary" /> {t('dashboard.gfsRetentionTitle')}
          </h3>
          {gfs ? (
            <p className="text-text-muted text-sm mt-2 font-mono">
              H{gfs.GH} / D{gfs.GD} / W{gfs.GW} / M{gfs.GM} / Y{gfs.GY}
            </p>
          ) : (
            <Spinner className="w-4 h-4 mt-2" />
          )}
          {isAdmin ? (
            <Link to="/admin" className="text-primary text-xs mt-3 inline-block hover:text-primary-hover transition-colors">{t('dashboard.editInAdmin')}</Link>
          ) : (
            <p className="text-text-muted text-xs mt-3">{t('dashboard.readOnlyHint')}</p>
          )}
        </div>
        <div className="bg-surface border border-border rounded-xl p-5">
          <h3 className="text-white font-medium flex items-center gap-2">
            <Bell className="w-4 h-4 text-primary" /> {t('dashboard.notifyTitle')}
          </h3>
          {notify ? (
            <p className="text-text-muted text-sm mt-2">
              Pushover: {notify.pushover_configured ? t('dashboard.configured') : t('dashboard.notConfigured')}
              {' · '}
              Slack: {notify.slack_configured ? t('dashboard.configured') : t('dashboard.notConfigured')}
            </p>
          ) : (
            <Spinner className="w-4 h-4 mt-2" />
          )}
          {isAdmin ? (
            <Link to="/admin" className="text-primary text-xs mt-3 inline-block hover:text-primary-hover transition-colors">{t('dashboard.editInAdmin')}</Link>
          ) : (
            <p className="text-text-muted text-xs mt-3">{t('dashboard.readOnlyHint')}</p>
          )}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
