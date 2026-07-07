import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Folder, File as FileIcon, Link2, Download, ChevronRight, Loader2 } from 'lucide-react';
import { fetchContainerSnapshots, browseSnapshot, buildFileDownloadUrl } from '../services/api';
import Modal from './Modal';
import Spinner from './Spinner';
import { formatBytes, formatEpoch } from '../utils/format';

const PAGE_SIZE = 200;

// Read-only file browser for one container's snapshot - lets an admin dig
// out a single file without downloading (and decompressing) the whole
// container archive, and without needing shell/SSH access to the vault
// host, which the people actually using this UI may not have or be
// allowed to. All path safety is enforced server-side (see
// vault_archive.resolve_safe_path) - this component just renders whatever
// the backend already validated.
const SnapshotBrowser = ({ host, container, onClose }) => {
  const { t } = useTranslation();
  const [snapshots, setSnapshots] = useState(null);
  const [snapshot, setSnapshot] = useState('');
  const [path, setPath] = useState('');
  const [listing, setListing] = useState(null); // { entries, total, offset, limit }
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchContainerSnapshots(host, container)
      .then((snaps) => {
        setSnapshots(snaps);
        if (snaps[0]) setSnapshot(snaps[0].name);
        else setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [host, container]);

  useEffect(() => {
    if (!snapshot) return;
    setLoading(true);
    setError(null);
    browseSnapshot(host, container, { snapshot, path, offset: 0, limit: PAGE_SIZE })
      .then(setListing)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [host, container, snapshot, path]);

  const loadMore = () => {
    if (!listing) return;
    const nextOffset = listing.offset + listing.entries.length;
    browseSnapshot(host, container, { snapshot, path, offset: nextOffset, limit: PAGE_SIZE })
      .then((more) => setListing((prev) => ({ ...more, entries: [...prev.entries, ...more.entries] })))
      .catch((err) => setError(err.message));
  };

  const crumbs = path ? path.split('/').filter(Boolean) : [];

  const goToCrumb = (index) => {
    setPath(crumbs.slice(0, index + 1).join('/'));
  };

  const openEntry = (entry) => {
    if (entry.is_dir) {
      setPath(path ? `${path}/${entry.name}` : entry.name);
    }
  };

  return (
    <Modal title={t('host.browseTitle', { container })} onClose={onClose} wide>
      {snapshots === null ? (
        <div className="flex items-center justify-center py-8"><Spinner className="w-6 h-6" /></div>
      ) : snapshots.length === 0 ? (
        <p className="text-text-muted text-sm">{t('host.noSnapshotsAvailable')}</p>
      ) : (
        <div>
          <div className="flex items-center gap-3 mb-3">
            <label className="text-sm text-text-muted shrink-0">{t('host.snapshotLabel')}</label>
            <select
              value={snapshot}
              onChange={(e) => { setSnapshot(e.target.value); setPath(''); }}
              className="flex-1 bg-background border border-border rounded px-3 py-1.5 text-text text-sm font-mono focus:outline-none focus:border-primary"
            >
              {snapshots.map((s) => (
                <option key={s.name} value={s.name}>{s.name} ({formatEpoch(s.creation_epoch)})</option>
              ))}
            </select>
          </div>

          <div className="flex items-center flex-wrap gap-1 text-xs text-text-muted mb-3 font-mono">
            <button onClick={() => setPath('')} className="hover:text-primary transition-colors">/</button>
            {crumbs.map((c, i) => (
              <React.Fragment key={i}>
                <ChevronRight className="w-3 h-3" />
                <button onClick={() => goToCrumb(i)} className="hover:text-primary transition-colors">{c}</button>
              </React.Fragment>
            ))}
          </div>

          {error && (
            <div className="mb-3 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">{error}</div>
          )}

          <div className="border border-border rounded-lg overflow-hidden max-h-[50vh] overflow-y-auto">
            {loading && !listing ? (
              <div className="flex items-center justify-center py-8"><Spinner className="w-6 h-6" /></div>
            ) : listing && listing.entries.length === 0 ? (
              <p className="text-text-muted text-sm text-center py-8">{t('host.emptyDirectory')}</p>
            ) : (
              <table className="w-full text-left text-sm">
                <tbody className="divide-y divide-border">
                  {listing?.entries.map((entry) => (
                    <tr key={entry.name} className="hover:bg-surface-hover transition-colors">
                      <td className="px-3 py-2 w-6">
                        {entry.is_symlink ? (
                          <Link2 className="w-4 h-4 text-text-muted" />
                        ) : entry.is_dir ? (
                          <Folder className="w-4 h-4 text-primary" />
                        ) : (
                          <FileIcon className="w-4 h-4 text-text-muted" />
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono">
                        {entry.is_dir ? (
                          <button onClick={() => openEntry(entry)} className="hover:text-primary transition-colors text-left">
                            {entry.name}
                          </button>
                        ) : (
                          entry.name
                        )}
                        {entry.is_symlink && (
                          <span className="text-text-muted text-xs ml-2">&rarr; {entry.symlink_target}</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-text-muted text-xs whitespace-nowrap">
                        {entry.size_bytes != null ? formatBytes(entry.size_bytes) : '—'}
                      </td>
                      <td className="px-3 py-2 text-right w-10">
                        {!entry.is_dir && (
                          <a
                            href={buildFileDownloadUrl(host, container, {
                              snapshot,
                              path: path ? `${path}/${entry.name}` : entry.name,
                            })}
                            className="inline-flex p-1 text-text-muted hover:text-primary hover:bg-primary/10 rounded transition-colors"
                            title={t('host.download')}
                          >
                            <Download className="w-3.5 h-3.5" />
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {listing && listing.entries.length < listing.total && (
            <button
              onClick={loadMore}
              className="mt-3 w-full flex items-center justify-center gap-2 px-3 py-2 bg-surface-hover hover:bg-border text-text-muted rounded-lg text-xs font-medium transition-colors"
            >
              <Loader2 className="w-3.5 h-3.5" />
              {t('host.loadMore', { shown: listing.entries.length, total: listing.total })}
            </button>
          )}
        </div>
      )}
    </Modal>
  );
};

export default SnapshotBrowser;
