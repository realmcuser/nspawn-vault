import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Users, Shield, Loader2, AlertCircle, Check, X, Network, HardDrive, Bell, Server, Trash2, Pencil, Plus, Copy, KeyRound, ScrollText, Play, Mail } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import {
  fetchUsers, updateUser, fetchAdminSettings, updateAdminSettings,
  fetchLdapSettings, updateLdapSettings, testLdapConnection,
  fetchGfsSettings, updateGfsSettings, fetchAdminNotifySettings, updateNotifySettings, sendTestEmail,
  fetchAdminHosts, createHost, deleteHost, updateHostContainers, updateHostEmails, updateHostTimer,
  testHostConnection, fetchVaultPublicKey, fetchAuditLog, triggerPruneNow,
} from '../services/api';

const EMPTY_LDAP = {
  enabled: false, server_url: '', base_dn: '', user_attr: 'uid',
  bind_dn_template: '', bind_dn: '', bind_password: '',
  required_group_dn: '', admin_group_dn: '', tls_verify: true,
};

const EMPTY_GFS = { GH: 24, GD: 7, GW: 4, GM: 12, GY: 3 };
const EMPTY_NOTIFY = {
  pushover_token: '', pushover_user: '', slack_url: '',
  smtp_host: '', smtp_port: '587', smtp_tls_mode: 'starttls', smtp_from: '',
  smtp_user: '', smtp_pass: '',
  ransomware_diff_threshold: '500',
};

const Admin = () => {
  const { t } = useTranslation();
  const { user: currentUser } = useAuth();

  const [users, setUsers] = useState([]);
  const [settings, setSettings] = useState({ allow_registration: true });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [savedRoleId, setSavedRoleId] = useState(null);

  const [ldap, setLdap] = useState(EMPTY_LDAP);
  const [ldapSaving, setLdapSaving] = useState(false);
  const [ldapError, setLdapError] = useState(null);
  const [ldapSuccess, setLdapSuccess] = useState(null);
  const [ldapTestResult, setLdapTestResult] = useState(null);
  const [ldapTesting, setLdapTesting] = useState(false);

  const [gfs, setGfs] = useState(EMPTY_GFS);
  const [gfsSaving, setGfsSaving] = useState(false);
  const [gfsError, setGfsError] = useState(null);
  const [gfsSuccess, setGfsSuccess] = useState(null);
  const [pruneTriggering, setPruneTriggering] = useState(false);
  const [pruneResult, setPruneResult] = useState(null);

  const [notify, setNotify] = useState(EMPTY_NOTIFY);
  const [notifySaving, setNotifySaving] = useState(false);
  const [notifyError, setNotifyError] = useState(null);
  const [notifySuccess, setNotifySuccess] = useState(null);
  const [testEmailTo, setTestEmailTo] = useState('');
  const [testingEmail, setTestingEmail] = useState(false);
  const [testEmailResult, setTestEmailResult] = useState(null);

  const [hosts, setHosts] = useState([]);
  const [hostsError, setHostsError] = useState(null);
  const [newHostName, setNewHostName] = useState('');
  const [newHostContainers, setNewHostContainers] = useState('');
  const [addingHost, setAddingHost] = useState(false);
  const [editingHost, setEditingHost] = useState(null); // host being container-edited
  const [editingContainersText, setEditingContainersText] = useState('');
  const [savingContainers, setSavingContainers] = useState(false);
  const [editingEmailsHost, setEditingEmailsHost] = useState(null);
  const [editingEmailsText, setEditingEmailsText] = useState('');
  const [savingEmails, setSavingEmails] = useState(false);
  const [testingHost, setTestingHost] = useState(null);
  const [hostTestResults, setHostTestResults] = useState({});
  const [testingNewHost, setTestingNewHost] = useState(false);
  const [newHostTestResult, setNewHostTestResult] = useState(null);
  const [vaultKey, setVaultKey] = useState({ exists: false, key: null });
  const [keyCopied, setKeyCopied] = useState(false);
  const [auditLog, setAuditLog] = useState(null); // { entries, total, offset, limit }
  const [auditLogError, setAuditLogError] = useState(null);

  useEffect(() => {
    loadData();
    fetchAuditLog(0, 50).then(setAuditLog).catch((err) => setAuditLogError(err.message));
  }, []);

  const loadMoreAuditLog = async () => {
    if (!auditLog) return;
    try {
      const next = await fetchAuditLog(auditLog.offset + auditLog.entries.length, auditLog.limit);
      setAuditLog((prev) => ({ ...next, entries: [...prev.entries, ...next.entries] }));
    } catch (err) {
      setAuditLogError(err.message);
    }
  };

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [usersData, settingsData, ldapData, gfsData, notifyData, hostsData, vaultKeyData] = await Promise.all([
        fetchUsers(),
        fetchAdminSettings(),
        fetchLdapSettings(),
        fetchGfsSettings(),
        fetchAdminNotifySettings(),
        fetchAdminHosts(),
        fetchVaultPublicKey(),
      ]);
      setUsers(usersData);
      setSettings(settingsData);
      // Unlike the internal build tool page this was adapted from, bind_password here is
      // never blanked — the backend returns a "********" sentinel (not the
      // real value) when a password is set, and PUT leaves the stored value
      // untouched unless this field is actually edited.
      setLdap((prev) => ({ ...prev, ...ldapData }));
      setGfs(gfsData);
      setNotify(notifyData);
      setHosts(hostsData);
      setVaultKey(vaultKeyData);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleToggleRegistration = async () => {
    setSaving(true);
    try {
      const newSettings = { allow_registration: !settings.allow_registration };
      await updateAdminSettings(newSettings);
      setSettings(newSettings);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleToggleUserActive = async (u) => {
    try {
      const updated = await updateUser(u.id, { is_active: !u.is_active });
      setUsers(users.map((x) => (x.id === u.id ? updated : x)));
    } catch (err) {
      alert(err.message);
    }
  };

  const handleChangeRole = async (u, newRole) => {
    try {
      const updated = await updateUser(u.id, { role: newRole });
      setUsers(users.map((x) => (x.id === u.id ? updated : x)));
      setSavedRoleId(u.id);
      setTimeout(() => setSavedRoleId(null), 1500);
    } catch (err) {
      alert(err.message);
    }
  };

  const handleLdapSave = async () => {
    setLdapSaving(true);
    setLdapError(null);
    setLdapSuccess(null);
    try {
      const saved = await updateLdapSettings(ldap);
      setLdap((prev) => ({ ...prev, ...saved }));
      setLdapSuccess(t('admin.ldap.saved'));
    } catch (err) {
      setLdapError(err.message);
    } finally {
      setLdapSaving(false);
    }
  };

  const handleLdapTest = async () => {
    setLdapTesting(true);
    setLdapTestResult(null);
    try {
      const result = await testLdapConnection({
        server_url: ldap.server_url,
        bind_dn: ldap.bind_dn || null,
        bind_password: ldap.bind_password && ldap.bind_password !== '********' ? ldap.bind_password : null,
        tls_verify: ldap.tls_verify,
      });
      setLdapTestResult(result);
    } catch (err) {
      setLdapTestResult({ success: false, message: err.message });
    } finally {
      setLdapTesting(false);
    }
  };

  const handleGfsSave = async () => {
    setGfsSaving(true);
    setGfsError(null);
    setGfsSuccess(null);
    try {
      const saved = await updateGfsSettings(gfs);
      setGfs(saved);
      setGfsSuccess(t('admin.gfs.saved'));
    } catch (err) {
      setGfsError(err.message);
    } finally {
      setGfsSaving(false);
    }
  };

  const handleTriggerPrune = async () => {
    setPruneTriggering(true);
    setPruneResult(null);
    try {
      await triggerPruneNow();
      setPruneResult({ success: true, message: t('admin.gfs.pruneStarted') });
    } catch (err) {
      setPruneResult({ success: false, message: err.message });
    } finally {
      setPruneTriggering(false);
    }
  };

  const handleNotifySave = async () => {
    setNotifySaving(true);
    setNotifyError(null);
    setNotifySuccess(null);
    try {
      const saved = await updateNotifySettings(notify);
      setNotify(saved);
      setNotifySuccess(t('admin.notify.saved'));
    } catch (err) {
      setNotifyError(err.message);
    } finally {
      setNotifySaving(false);
    }
  };

  const handleSendTestEmail = async () => {
    const to = testEmailTo.trim();
    if (!to) return;
    setTestingEmail(true);
    setTestEmailResult(null);
    try {
      const result = await sendTestEmail(to);
      setTestEmailResult(result);
    } catch (err) {
      setTestEmailResult({ success: false, message: err.message });
    } finally {
      setTestingEmail(false);
    }
  };

  const parseContainersText = (text) =>
    text.split('\n').map((s) => s.trim()).filter(Boolean);

  const parseEmailsText = (text) =>
    text.split('\n').map((s) => s.trim()).filter(Boolean);

  const handleAddHost = async () => {
    const host = newHostName.trim();
    if (!host) return;
    setHostsError(null);
    setAddingHost(true);
    try {
      const created = await createHost(host, parseContainersText(newHostContainers));
      setHosts([...hosts, created]);
      setNewHostName('');
      setNewHostContainers('');
    } catch (err) {
      setHostsError(err.message);
    } finally {
      setAddingHost(false);
    }
  };

  const handleDeleteHost = async (host) => {
    if (!window.confirm(t('admin.hosts.confirmDelete', { host }))) return;
    setHostsError(null);
    try {
      await deleteHost(host);
      setHosts(hosts.filter((h) => h.host !== host));
    } catch (err) {
      setHostsError(err.message);
    }
  };

  const handleStartEditContainers = (h) => {
    setEditingHost(h.host);
    setEditingContainersText(h.containers.join('\n'));
  };

  const handleSaveContainers = async (host) => {
    setSavingContainers(true);
    setHostsError(null);
    try {
      const containers = parseContainersText(editingContainersText);
      const updated = await updateHostContainers(host, containers);
      setHosts(hosts.map((h) => (h.host === host ? { ...h, containers: updated.containers } : h)));
      setEditingHost(null);
    } catch (err) {
      setHostsError(err.message);
    } finally {
      setSavingContainers(false);
    }
  };

  const handleStartEditEmails = (h) => {
    setEditingEmailsHost(h.host);
    setEditingEmailsText((h.emails || []).join('\n'));
  };

  const handleSaveEmails = async (host) => {
    setSavingEmails(true);
    setHostsError(null);
    try {
      const emails = parseEmailsText(editingEmailsText);
      const updated = await updateHostEmails(host, emails);
      setHosts(hosts.map((h) => (h.host === host ? { ...h, emails: updated.emails } : h)));
      setEditingEmailsHost(null);
    } catch (err) {
      setHostsError(err.message);
    } finally {
      setSavingEmails(false);
    }
  };

  const handleCopyVaultKey = async () => {
    if (!vaultKey.key) return;
    await navigator.clipboard.writeText(vaultKey.key);
    setKeyCopied(true);
    setTimeout(() => setKeyCopied(false), 2000);
  };

  const handleTestHostConnection = async (host) => {
    setTestingHost(host);
    setHostTestResults((prev) => ({ ...prev, [host]: null }));
    try {
      const result = await testHostConnection(host);
      setHostTestResults((prev) => ({ ...prev, [host]: result }));
    } catch (err) {
      setHostTestResults((prev) => ({ ...prev, [host]: { success: false, message: err.message } }));
    } finally {
      setTestingHost(null);
    }
  };

  const handleTestNewHost = async () => {
    const host = newHostName.trim();
    if (!host) return;
    setTestingNewHost(true);
    setNewHostTestResult(null);
    try {
      const result = await testHostConnection(host);
      setNewHostTestResult(result);
    } catch (err) {
      setNewHostTestResult({ success: false, message: err.message });
    } finally {
      setTestingNewHost(false);
    }
  };

  const handleToggleTimer = async (h) => {
    setHostsError(null);
    try {
      const updated = await updateHostTimer(h.host, !h.timer_enabled);
      setHosts(hosts.map((x) => (x.host === h.host ? { ...x, timer_enabled: updated.timer_enabled } : x)));
    } catch (err) {
      setHostsError(err.message);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-8 max-w-4xl mx-auto">
      <div>
        <h2 className="text-3xl font-bold tracking-tight mb-2">{t('sidebar.admin')}</h2>
        <p className="text-text-muted">{t('admin.description')}</p>
      </div>

      {error && (
        <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-2 text-red-400">
          <AlertCircle className="w-5 h-5" />
          {error}
        </div>
      )}

      {/* Registration Setting */}
      <div className="bg-surface border border-border rounded-xl p-6">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Shield className="w-5 h-5" />
          {t('admin.registration')}
        </h3>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-text">{t('admin.allowRegistration')}</p>
            <p className="text-sm text-text-muted">{t('admin.allowRegistrationHint')}</p>
          </div>
          <button
            onClick={handleToggleRegistration}
            disabled={saving}
            className={`relative w-14 h-7 rounded-full transition-colors ${
              settings.allow_registration ? 'bg-primary' : 'bg-surface-hover'
            }`}
          >
            <span
              className={`absolute top-1 w-5 h-5 bg-white rounded-full transition-transform ${
                settings.allow_registration ? 'left-8' : 'left-1'
              }`}
            />
          </button>
        </div>
      </div>

      {/* User Management */}
      <div className="bg-surface border border-border rounded-xl p-6">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Users className="w-5 h-5" />
          {t('admin.userManagement')}
        </h3>

        <div className="border border-border rounded-lg overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-hover text-text-muted border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium">{t('admin.username')}</th>
                <th className="px-4 py-3 font-medium">{t('admin.role')}</th>
                <th className="px-4 py-3 font-medium">{t('admin.status')}</th>
                <th className="px-4 py-3 font-medium text-right">{t('admin.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {users.map((u) => (
                <tr key={u.id} className="hover:bg-surface-hover transition-colors">
                  <td className="px-4 py-3 font-mono">
                    {u.username}
                    {u.id === currentUser.id && (
                      <span className="ml-2 text-xs text-primary">({t('admin.you')})</span>
                    )}
                    {u.auth_source === 'ldap' && (
                      <span className="ml-2 text-xs px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 font-sans">LDAP</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <select
                        value={u.role}
                        onChange={(e) => handleChangeRole(u, e.target.value)}
                        disabled={u.id === currentUser.id}
                        className="bg-background border border-border rounded px-2 py-1 text-text text-sm focus:outline-none focus:border-primary disabled:opacity-50"
                      >
                        <option value="admin">Admin</option>
                        <option value="user">User</option>
                      </select>
                      {savedRoleId === u.id && <Check className="w-4 h-4 text-green-500" />}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${
                      u.is_active ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500'
                    }`}>
                      {u.is_active ? (<><Check className="w-3 h-3" /> {t('admin.active')}</>) : (<><X className="w-3 h-3" /> {t('admin.inactive')}</>)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => handleToggleUserActive(u)}
                      disabled={u.id === currentUser.id}
                      className={`px-3 py-1 rounded text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                        u.is_active ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20' : 'bg-green-500/10 text-green-400 hover:bg-green-500/20'
                      }`}
                    >
                      {u.is_active ? t('admin.deactivate') : t('admin.activate')}
                    </button>
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan="4" className="px-4 py-8 text-center text-text-muted">{t('admin.noUsers')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Source Hosts */}
      <div className="bg-surface border border-border rounded-xl p-6">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Server className="w-5 h-5" />
          {t('admin.hosts.title')}
        </h3>
        <p className="text-sm text-text-muted mb-4">{t('admin.hosts.description')}</p>

        <div className="mb-4 p-4 bg-background/50 border border-border rounded-lg">
          <p className="text-sm font-medium text-text flex items-center gap-2 mb-2">
            <KeyRound className="w-4 h-4 text-primary" />
            {t('admin.hosts.vaultKeyTitle')}
          </p>
          <p className="text-xs text-text-muted mb-3">{t('admin.hosts.vaultKeyHint')}</p>
          {vaultKey.exists ? (
            <div className="flex items-start gap-2">
              <code className="flex-1 min-w-0 block px-3 py-2 bg-background border border-border rounded text-xs font-mono text-text-muted overflow-x-auto whitespace-nowrap">
                {vaultKey.key}
              </code>
              <button
                onClick={handleCopyVaultKey}
                className="flex items-center gap-1.5 px-3 py-2 bg-surface-hover hover:bg-border text-text rounded text-xs font-medium transition-colors shrink-0"
              >
                {keyCopied ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
                {keyCopied ? t('admin.hosts.copied') : t('admin.hosts.copy')}
              </button>
            </div>
          ) : (
            <p className="text-xs text-yellow-400">{t('admin.hosts.vaultKeyMissing')}</p>
          )}
        </div>

        {hostsError && (
          <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />{hostsError}
          </div>
        )}

        <div className="border border-border rounded-lg overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-hover text-text-muted border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium">{t('admin.hosts.colHost')}</th>
                <th className="px-4 py-3 font-medium">{t('admin.hosts.colContainers')}</th>
                <th className="px-4 py-3 font-medium">{t('admin.hosts.colEmails')}</th>
                <th className="px-4 py-3 font-medium">{t('admin.hosts.colTimer')}</th>
                <th className="px-4 py-3 font-medium text-right">{t('admin.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {hosts.map((h) => (
                <tr key={h.host} className="hover:bg-surface-hover transition-colors align-top">
                  <td className="px-4 py-3 font-mono">
                    <div>{h.host}</div>
                    <button
                      onClick={() => handleTestHostConnection(h.host)}
                      disabled={testingHost === h.host}
                      className="mt-1 flex items-center gap-1 text-xs text-text-muted hover:text-primary transition-colors font-sans disabled:opacity-50"
                    >
                      {testingHost === h.host ? <Loader2 className="w-3 h-3 animate-spin" /> : <Network className="w-3 h-3" />}
                      {t('admin.hosts.testConnection')}
                    </button>
                    {hostTestResults[h.host] && (
                      <p className={`mt-1 text-xs font-sans ${hostTestResults[h.host].success ? 'text-green-400' : 'text-red-400'}`}>
                        {hostTestResults[h.host].message}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {editingHost === h.host ? (
                      <div className="space-y-2">
                        <textarea
                          value={editingContainersText}
                          onChange={(e) => setEditingContainersText(e.target.value)}
                          rows={Math.max(2, editingContainersText.split('\n').length)}
                          className="w-full bg-background border border-border rounded px-2 py-1 text-text text-sm font-mono focus:outline-none focus:border-primary"
                          placeholder={t('admin.hosts.oneContainerPerLine')}
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleSaveContainers(h.host)}
                            disabled={savingContainers}
                            className="flex items-center gap-1 px-2 py-1 bg-primary hover:bg-primary-hover text-white rounded text-xs font-medium transition-colors disabled:opacity-50"
                          >
                            {savingContainers ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                            {t('admin.gfs.save')}
                          </button>
                          <button
                            onClick={() => setEditingHost(null)}
                            className="px-2 py-1 bg-surface-hover hover:bg-border text-text rounded text-xs font-medium transition-colors"
                          >
                            {t('admin.hosts.cancel')}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 flex-wrap">
                        {h.containers.length === 0 ? (
                          <span className="text-text-muted italic">{t('admin.hosts.noContainers')}</span>
                        ) : (
                          h.containers.map((c) => (
                            <span key={c} className="px-2 py-0.5 rounded bg-surface-hover text-text text-xs font-mono">{c}</span>
                          ))
                        )}
                        <button
                          onClick={() => handleStartEditContainers(h)}
                          className="p-1 text-text-muted hover:text-primary hover:bg-primary/10 rounded transition-colors"
                          title={t('admin.hosts.editContainers')}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {editingEmailsHost === h.host ? (
                      <div className="space-y-2">
                        <textarea
                          value={editingEmailsText}
                          onChange={(e) => setEditingEmailsText(e.target.value)}
                          rows={Math.max(2, editingEmailsText.split('\n').length)}
                          className="w-full bg-background border border-border rounded px-2 py-1 text-text text-sm font-mono focus:outline-none focus:border-primary"
                          placeholder={t('admin.hosts.oneEmailPerLine')}
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleSaveEmails(h.host)}
                            disabled={savingEmails}
                            className="flex items-center gap-1 px-2 py-1 bg-primary hover:bg-primary-hover text-white rounded text-xs font-medium transition-colors disabled:opacity-50"
                          >
                            {savingEmails ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                            {t('admin.gfs.save')}
                          </button>
                          <button
                            onClick={() => setEditingEmailsHost(null)}
                            className="px-2 py-1 bg-surface-hover hover:bg-border text-text rounded text-xs font-medium transition-colors"
                          >
                            {t('admin.hosts.cancel')}
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 flex-wrap">
                        {(h.emails || []).length === 0 ? (
                          <span className="text-text-muted italic">{t('admin.hosts.noEmails')}</span>
                        ) : (
                          h.emails.map((e) => (
                            <span key={e} className="px-2 py-0.5 rounded bg-surface-hover text-text text-xs font-mono">{e}</span>
                          ))
                        )}
                        <button
                          onClick={() => handleStartEditEmails(h)}
                          className="p-1 text-text-muted hover:text-primary hover:bg-primary/10 rounded transition-colors"
                          title={t('admin.hosts.editEmails')}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => handleToggleTimer(h)}
                      className={`relative w-11 h-6 rounded-full transition-colors ${h.timer_enabled ? 'bg-primary' : 'bg-surface-hover'}`}
                      title={h.timer_enabled ? t('admin.hosts.timerEnabled') : t('admin.hosts.timerDisabled')}
                    >
                      <span className={`absolute top-1 w-4 h-4 bg-white rounded-full transition-transform ${h.timer_enabled ? 'left-6' : 'left-1'}`} />
                    </button>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => handleDeleteHost(h.host)}
                      className="p-1.5 text-text-muted hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors"
                      title={t('admin.hosts.deleteHost')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {hosts.length === 0 && (
                <tr>
                  <td colSpan="5" className="px-4 py-8 text-center text-text-muted">{t('admin.hosts.noHosts')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-4 pt-4 border-t border-border">
          <p className="text-sm font-medium text-text-muted mb-3">{t('admin.hosts.addHost')}</p>
          <div className="grid grid-cols-2 gap-4 mb-3">
            <div>
              <label className="block text-sm text-text-muted mb-1">{t('admin.hosts.hostLabel')}</label>
              <input
                type="text"
                value={newHostName}
                onChange={(e) => setNewHostName(e.target.value)}
                placeholder="newclient.example.com"
                className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm font-mono focus:outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="block text-sm text-text-muted mb-1">{t('admin.hosts.containersLabel')}</label>
              <textarea
                value={newHostContainers}
                onChange={(e) => setNewHostContainers(e.target.value)}
                rows={2}
                placeholder={t('admin.hosts.oneContainerPerLine')}
                className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm font-mono focus:outline-none focus:border-primary"
              />
            </div>
          </div>
          <div className="flex items-start gap-2 p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg mb-3">
            <AlertCircle className="w-4 h-4 text-yellow-500 flex-shrink-0 mt-0.5" />
            <p className="text-sm text-yellow-200">{t('admin.hosts.sshTrustWarning')}</p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleAddHost}
              disabled={addingHost || !newHostName.trim()}
              className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {addingHost ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              {t('admin.hosts.addHost')}
            </button>
            <button
              onClick={handleTestNewHost}
              disabled={testingNewHost || !newHostName.trim()}
              className="flex items-center gap-2 px-4 py-2 bg-surface-hover hover:bg-border text-text rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {testingNewHost ? <Loader2 className="w-4 h-4 animate-spin" /> : <Network className="w-4 h-4" />}
              {t('admin.hosts.testConnection')}
            </button>
          </div>
          {newHostTestResult && (
            <div className={`mt-3 p-3 rounded-lg text-sm flex items-center gap-2 ${newHostTestResult.success ? 'bg-green-500/10 border border-green-500/20 text-green-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'}`}>
              {newHostTestResult.success ? <Check className="w-4 h-4 shrink-0" /> : <X className="w-4 h-4 shrink-0" />}
              {newHostTestResult.message}
            </div>
          )}
        </div>
      </div>

      {/* GFS Retention */}
      <div className="bg-surface border border-border rounded-xl p-6">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <HardDrive className="w-5 h-5" />
          {t('admin.gfs.title')}
        </h3>
        <p className="text-sm text-text-muted mb-4">{t('admin.gfs.description')}</p>

        {gfsError && (
          <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />{gfsError}
          </div>
        )}
        {gfsSuccess && (
          <div className="mb-4 p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-green-400 text-sm flex items-center gap-2">
            <Check className="w-4 h-4 shrink-0" />{gfsSuccess}
          </div>
        )}

        <div className="grid grid-cols-5 gap-3">
          {[
            ['GH', t('admin.gfs.hourly')],
            ['GD', t('admin.gfs.daily')],
            ['GW', t('admin.gfs.weekly')],
            ['GM', t('admin.gfs.monthly')],
            ['GY', t('admin.gfs.yearly')],
          ].map(([key, label]) => (
            <div key={key}>
              <label className="block text-sm text-text-muted mb-1">{label}</label>
              <input
                type="number"
                min="0"
                max="10000"
                value={gfs[key]}
                onChange={(e) => setGfs((p) => ({ ...p, [key]: parseInt(e.target.value, 10) || 0 }))}
                className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
              />
            </div>
          ))}
        </div>

        <div className="pt-4 flex items-center gap-3 flex-wrap">
          <button
            onClick={handleGfsSave}
            disabled={gfsSaving}
            className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {gfsSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
            {t('admin.gfs.save')}
          </button>
          <button
            onClick={handleTriggerPrune}
            disabled={pruneTriggering}
            title={t('admin.gfs.pruneNowHint')}
            className="flex items-center gap-2 px-4 py-2 bg-surface-hover hover:bg-border text-text rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {pruneTriggering ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {t('admin.gfs.pruneNow')}
          </button>
        </div>

        {pruneResult && (
          <div className={`mt-4 p-3 rounded-lg text-sm flex items-center gap-2 ${pruneResult.success ? 'bg-green-500/10 border border-green-500/20 text-green-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'}`}>
            {pruneResult.success ? <Check className="w-4 h-4 shrink-0" /> : null}
            {pruneResult.message}
          </div>
        )}
      </div>

      {/* Notifications */}
      <div className="bg-surface border border-border rounded-xl p-6">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Bell className="w-5 h-5" />
          {t('admin.notify.title')}
        </h3>
        <p className="text-sm text-text-muted mb-4">{t('admin.notify.description')}</p>

        {notifyError && (
          <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />{notifyError}
          </div>
        )}
        {notifySuccess && (
          <div className="mb-4 p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-green-400 text-sm flex items-center gap-2">
            <Check className="w-4 h-4 shrink-0" />{notifySuccess}
          </div>
        )}

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-text-muted mb-1">{t('admin.notify.pushoverToken')}</label>
              <input
                type="password"
                value={notify.pushover_token}
                onChange={(e) => setNotify((p) => ({ ...p, pushover_token: e.target.value }))}
                placeholder={t('admin.notify.secretPlaceholder')}
                className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="block text-sm text-text-muted mb-1">{t('admin.notify.pushoverUser')}</label>
              <input
                type="password"
                value={notify.pushover_user}
                onChange={(e) => setNotify((p) => ({ ...p, pushover_user: e.target.value }))}
                placeholder={t('admin.notify.secretPlaceholder')}
                className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
              />
            </div>
          </div>
          <div>
            <label className="block text-sm text-text-muted mb-1">{t('admin.notify.slackUrl')}</label>
            <input
              type="password"
              value={notify.slack_url}
              onChange={(e) => setNotify((p) => ({ ...p, slack_url: e.target.value }))}
              placeholder={t('admin.notify.secretPlaceholder')}
              className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
            />
          </div>

          <div className="border-t border-border pt-4">
            <p className="text-sm font-medium text-text-muted mb-1">{t('admin.notify.smtpTitle')}</p>
            <p className="text-xs text-text-muted mb-3">{t('admin.notify.smtpHint')}</p>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.notify.smtpHost')}</label>
                <input
                  type="text"
                  value={notify.smtp_host}
                  onChange={(e) => setNotify((p) => ({ ...p, smtp_host: e.target.value }))}
                  placeholder="relay.example.com"
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
                />
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.notify.smtpPort')}</label>
                <input
                  type="text"
                  value={notify.smtp_port}
                  onChange={(e) => setNotify((p) => ({ ...p, smtp_port: e.target.value }))}
                  placeholder="587"
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.notify.smtpTlsMode')}</label>
                <select
                  value={notify.smtp_tls_mode}
                  onChange={(e) => setNotify((p) => ({ ...p, smtp_tls_mode: e.target.value }))}
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
                >
                  <option value="starttls">{t('admin.notify.smtpTlsStarttls')}</option>
                  <option value="implicit">{t('admin.notify.smtpTlsImplicit')}</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.notify.smtpFrom')}</label>
                <input
                  type="text"
                  value={notify.smtp_from}
                  onChange={(e) => setNotify((p) => ({ ...p, smtp_from: e.target.value }))}
                  placeholder="nspawn-vault@example.com"
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.notify.smtpUser')}</label>
                <input
                  type="text"
                  value={notify.smtp_user}
                  onChange={(e) => setNotify((p) => ({ ...p, smtp_user: e.target.value }))}
                  placeholder={t('admin.notify.secretPlaceholder')}
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
                />
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.notify.smtpPass')}</label>
                <input
                  type="password"
                  value={notify.smtp_pass}
                  onChange={(e) => setNotify((p) => ({ ...p, smtp_pass: e.target.value }))}
                  placeholder={t('admin.notify.secretPlaceholder')}
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
                />
              </div>
            </div>
          </div>

          <div className="border-t border-border pt-4">
            <p className="text-sm font-medium text-text-muted mb-1">{t('admin.notify.ransomwareTitle')}</p>
            <p className="text-xs text-text-muted mb-3">{t('admin.notify.ransomwareHint')}</p>
            <div className="max-w-xs">
              <label className="block text-sm text-text-muted mb-1">{t('admin.notify.ransomwareThreshold')}</label>
              <input
                type="text"
                inputMode="numeric"
                value={notify.ransomware_diff_threshold}
                onChange={(e) => setNotify((p) => ({ ...p, ransomware_diff_threshold: e.target.value }))}
                placeholder="500"
                className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
              />
            </div>
          </div>

          <div className="pt-2 flex items-center gap-3">
            <button
              onClick={handleNotifySave}
              disabled={notifySaving}
              className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {notifySaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              {t('admin.notify.save')}
            </button>
          </div>

          <div className="border-t border-border pt-4">
            <p className="text-sm font-medium text-text-muted mb-1">{t('admin.notify.testEmailTitle')}</p>
            <p className="text-xs text-text-muted mb-3">{t('admin.notify.testEmailHint')}</p>
            <div className="flex items-center gap-3">
              <input
                type="email"
                value={testEmailTo}
                onChange={(e) => setTestEmailTo(e.target.value)}
                placeholder="you@example.com"
                className="flex-1 bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
              />
              <button
                onClick={handleSendTestEmail}
                disabled={testingEmail || !testEmailTo.trim()}
                className="flex items-center gap-2 px-4 py-2 bg-surface-hover hover:bg-border text-text rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
              >
                {testingEmail ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mail className="w-4 h-4" />}
                {t('admin.notify.sendTestEmail')}
              </button>
            </div>
            {testEmailResult && (
              <div className={`mt-3 p-3 rounded-lg text-sm flex items-center gap-2 ${testEmailResult.success ? 'bg-green-500/10 border border-green-500/20 text-green-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'}`}>
                {testEmailResult.success ? <Check className="w-4 h-4 shrink-0" /> : <X className="w-4 h-4 shrink-0" />}
                {testEmailResult.message}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* LDAP Settings */}
      <div className="bg-surface border border-border rounded-xl p-6">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Network className="w-5 h-5" />
          {t('admin.ldap.title')}
        </h3>

        {ldapError && (
          <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />{ldapError}
          </div>
        )}
        {ldapSuccess && (
          <div className="mb-4 p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-green-400 text-sm flex items-center gap-2">
            <Check className="w-4 h-4 shrink-0" />{ldapSuccess}
          </div>
        )}

        <div className="flex items-center justify-between mb-6">
          <div>
            <p className="text-text">{t('admin.ldap.enable')}</p>
            <p className="text-sm text-text-muted">{t('admin.ldap.enableHint')}</p>
          </div>
          <button
            onClick={() => setLdap((p) => ({ ...p, enabled: !p.enabled }))}
            className={`relative w-14 h-7 rounded-full transition-colors ${ldap.enabled ? 'bg-primary' : 'bg-surface-hover'}`}
          >
            <span className={`absolute top-1 w-5 h-5 bg-white rounded-full transition-transform ${ldap.enabled ? 'left-8' : 'left-1'}`} />
          </button>
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm text-text-muted mb-1">{t('admin.ldap.serverUrl')}</label>
              <input
                type="text"
                value={ldap.server_url || ''}
                onChange={(e) => setLdap((p) => ({ ...p, server_url: e.target.value }))}
                placeholder="ldaps://ipa.example.com"
                className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="block text-sm text-text-muted mb-1">{t('admin.ldap.baseDn')}</label>
              <input
                type="text"
                value={ldap.base_dn || ''}
                onChange={(e) => setLdap((p) => ({ ...p, base_dn: e.target.value }))}
                placeholder="dc=example,dc=com"
                className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm text-text-muted mb-1">{t('admin.ldap.userAttr')}</label>
            <select
              value={ldap.user_attr || 'uid'}
              onChange={(e) => setLdap((p) => ({ ...p, user_attr: e.target.value }))}
              className="bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
            >
              <option value="uid">uid (FreeIPA / OpenLDAP)</option>
              <option value="sAMAccountName">sAMAccountName (Active Directory)</option>
              <option value="cn">cn</option>
            </select>
          </div>

          <div>
            <label className="block text-sm text-text-muted mb-1">{t('admin.ldap.bindDnTemplate')}</label>
            <input
              type="text"
              value={ldap.bind_dn_template || ''}
              onChange={(e) => setLdap((p) => ({ ...p, bind_dn_template: e.target.value }))}
              placeholder="uid={username},cn=users,cn=accounts,dc=example,dc=com"
              className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
            />
            <p className="text-xs text-text-muted mt-1">{t('admin.ldap.bindDnTemplateHint')}</p>
          </div>

          <div className="border-t border-border pt-4">
            <p className="text-sm font-medium text-text-muted mb-3">{t('admin.ldap.serviceAccount')}</p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.ldap.bindDn')}</label>
                <input
                  type="text"
                  value={ldap.bind_dn || ''}
                  onChange={(e) => setLdap((p) => ({ ...p, bind_dn: e.target.value }))}
                  placeholder="cn=svc-nspawn-vault,dc=example,dc=com"
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
                />
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.ldap.bindPassword')}</label>
                <input
                  type="password"
                  value={ldap.bind_password || ''}
                  onChange={(e) => setLdap((p) => ({ ...p, bind_password: e.target.value }))}
                  placeholder={t('admin.ldap.bindPasswordPlaceholder')}
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary"
                />
              </div>
            </div>
          </div>

          <div className="border-t border-border pt-4">
            <p className="text-sm font-medium text-text-muted mb-3">{t('admin.ldap.groups')}</p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.ldap.requiredGroup')}</label>
                <input
                  type="text"
                  value={ldap.required_group_dn || ''}
                  onChange={(e) => setLdap((p) => ({ ...p, required_group_dn: e.target.value }))}
                  placeholder="cn=vault-users,cn=groups,cn=accounts,dc=..."
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
                />
                <p className="text-xs text-text-muted mt-1">{t('admin.ldap.requiredGroupHint')}</p>
              </div>
              <div>
                <label className="block text-sm text-text-muted mb-1">{t('admin.ldap.adminGroup')}</label>
                <input
                  type="text"
                  value={ldap.admin_group_dn || ''}
                  onChange={(e) => setLdap((p) => ({ ...p, admin_group_dn: e.target.value }))}
                  placeholder="cn=vault-admins,cn=groups,cn=accounts,dc=..."
                  className="w-full bg-background border border-border rounded px-3 py-2 text-text text-sm focus:outline-none focus:border-primary font-mono"
                />
                <p className="text-xs text-text-muted mt-1">{t('admin.ldap.adminGroupHint')}</p>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="tls-verify"
              checked={ldap.tls_verify}
              onChange={(e) => setLdap((p) => ({ ...p, tls_verify: e.target.checked }))}
              className="w-4 h-4 accent-primary"
            />
            <label htmlFor="tls-verify" className="text-sm text-text">{t('admin.ldap.tlsVerify')}</label>
          </div>

          {ldapTestResult && (
            <div className={`p-3 rounded-lg text-sm flex items-center gap-2 ${ldapTestResult.success ? 'bg-green-500/10 border border-green-500/20 text-green-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'}`}>
              {ldapTestResult.success ? <Check className="w-4 h-4 shrink-0" /> : <X className="w-4 h-4 shrink-0" />}
              {ldapTestResult.message}
            </div>
          )}

          <div className="flex gap-3 pt-2">
            <button
              onClick={handleLdapTest}
              disabled={ldapTesting || !ldap.server_url}
              className="flex items-center gap-2 px-4 py-2 bg-surface-hover hover:bg-border text-text rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {ldapTesting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Network className="w-4 h-4" />}
              {t('admin.ldap.testConnection')}
            </button>
            <button
              onClick={handleLdapSave}
              disabled={ldapSaving}
              className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {ldapSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              {t('admin.ldap.save')}
            </button>
          </div>
        </div>
      </div>

      {/* Audit Log */}
      <div className="bg-surface border border-border rounded-xl p-6">
        <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <ScrollText className="w-5 h-5" />
          {t('admin.audit.title')}
        </h3>
        <p className="text-sm text-text-muted mb-4">{t('admin.audit.description')}</p>

        {auditLogError && (
          <div className="mb-4 p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />{auditLogError}
          </div>
        )}

        {!auditLog ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
          </div>
        ) : auditLog.entries.length === 0 ? (
          <p className="text-text-muted text-sm text-center py-8">{t('admin.audit.empty')}</p>
        ) : (
          <>
            <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-surface-hover text-text-muted border-b border-border">
                  <tr>
                    <th className="px-4 py-3 font-medium whitespace-nowrap">{t('admin.audit.colTime')}</th>
                    <th className="px-4 py-3 font-medium">{t('admin.audit.colUser')}</th>
                    <th className="px-4 py-3 font-medium">{t('admin.audit.colAction')}</th>
                    <th className="px-4 py-3 font-medium">{t('admin.audit.colHost')}</th>
                    <th className="px-4 py-3 font-medium">{t('admin.audit.colContainer')}</th>
                    <th className="px-4 py-3 font-medium">{t('admin.audit.colDetail')}</th>
                    <th className="px-4 py-3 font-medium">{t('admin.audit.colIp')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {auditLog.entries.map((e, i) => (
                    <tr key={i} className="hover:bg-surface-hover transition-colors">
                      <td className="px-4 py-3 font-mono text-xs whitespace-nowrap">{new Date(e.timestamp).toLocaleString()}</td>
                      <td className="px-4 py-3 font-mono">{e.username}</td>
                      <td className="px-4 py-3">
                        <span className="px-2 py-0.5 rounded bg-primary/10 text-primary text-xs font-medium">
                          {t(`admin.audit.action.${e.action}`)}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-text-muted text-xs">{e.host}</td>
                      <td className="px-4 py-3 font-mono text-text-muted text-xs">{e.container}{e.snapshot ? `@${e.snapshot}` : ''}</td>
                      <td className="px-4 py-3 font-mono text-text-muted text-xs">{e.path || e.detail || '—'}</td>
                      <td className="px-4 py-3 font-mono text-text-muted text-xs">{e.client_ip || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {auditLog.entries.length < auditLog.total && (
              <button
                onClick={loadMoreAuditLog}
                className="mt-3 w-full flex items-center justify-center gap-2 px-3 py-2 bg-surface-hover hover:bg-border text-text-muted rounded-lg text-xs font-medium transition-colors"
              >
                {t('host.loadMore', { shown: auditLog.entries.length, total: auditLog.total })}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default Admin;
