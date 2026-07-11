// src/services/api.js

const API_URL = import.meta.env.VITE_API_URL || '';

// Helper to get token
export const getToken = () => localStorage.getItem('token');

// Helper for authenticated requests
export async function fetchWithAuth(endpoint, options = {}) {
  const token = getToken();
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    // Handle unauthorized (e.g., token expired)
    localStorage.removeItem('token');
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }

  return response;
}

export async function login(username, password) {
  const formData = new URLSearchParams();
  formData.append('username', username);
  formData.append('password', password);

  const response = await fetch(`${API_URL}/api/token`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Login failed');
  }
  return response.json();
}

export async function register(username, password) {
  const response = await fetch(`${API_URL}/api/users`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Registration failed');
  }
  return response.json();
}

// Check if registration is allowed (public endpoint)
export async function checkRegistrationAllowed() {
  const response = await fetch(`${API_URL}/api/settings/registration`);
  if (!response.ok) throw new Error('Failed to check registration status');
  return response.json();
}

// Get current user info
export async function fetchCurrentUser() {
  const response = await fetchWithAuth('/api/users/me');
  if (!response.ok) throw new Error('Failed to fetch user info');
  return response.json();
}

// Admin: Get all users
export async function fetchUsers() {
  const response = await fetchWithAuth('/api/admin/users');
  if (!response.ok) throw new Error('Failed to fetch users');
  return response.json();
}

// Admin: Update user
export async function updateUser(userId, data) {
  const response = await fetchWithAuth(`/api/admin/users/${userId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to update user');
  }
  return response.json();
}

// Admin: Get/update system settings (allow_registration)
export async function fetchAdminSettings() {
  const response = await fetchWithAuth('/api/admin/settings');
  if (!response.ok) throw new Error('Failed to fetch settings');
  return response.json();
}

export async function updateAdminSettings(settings) {
  const response = await fetchWithAuth('/api/admin/settings', {
    method: 'PUT',
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw new Error('Failed to update settings');
  return response.json();
}

// LDAP settings (admin only)
export async function fetchLdapSettings() {
  const response = await fetchWithAuth('/api/admin/ldap');
  if (!response.ok) throw new Error('Failed to fetch LDAP settings');
  return response.json();
}

export async function updateLdapSettings(data) {
  const response = await fetchWithAuth('/api/admin/ldap', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to save LDAP settings');
  return response.json();
}

export async function testLdapConnection(data) {
  const response = await fetchWithAuth('/api/admin/ldap/test', {
    method: 'POST',
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('Failed to test LDAP connection');
  return response.json();
}

// --- nspawn-vault domain data (new, no rpmworks equivalent) ---

export async function fetchHosts() {
  const response = await fetchWithAuth('/api/hosts');
  if (!response.ok) throw new Error('Failed to fetch hosts');
  return response.json();
}

export async function fetchHostDetail(host) {
  const response = await fetchWithAuth(`/api/hosts/${encodeURIComponent(host)}`);
  if (!response.ok) throw new Error('Failed to fetch host detail');
  return response.json();
}

export async function fetchGfsSettings() {
  const response = await fetchWithAuth('/api/settings/gfs');
  if (!response.ok) throw new Error('Failed to fetch GFS settings');
  return response.json();
}

export async function fetchNotifySettings() {
  const response = await fetchWithAuth('/api/settings/notify');
  if (!response.ok) throw new Error('Failed to fetch notify settings');
  return response.json();
}

export async function fetchAlertsSummary() {
  const response = await fetchWithAuth('/api/alerts/summary');
  if (!response.ok) throw new Error('Failed to fetch alerts summary');
  return response.json();
}

// Vault-wide ZFS pool used/available bytes - not any one host's slice of it.
export async function fetchVaultStorage() {
  const response = await fetchWithAuth('/api/vault/storage');
  if (!response.ok) throw new Error('Failed to fetch vault storage');
  return response.json();
}

// Admin: GFS retention (no secrets involved, so no masking needed)
export async function updateGfsSettings(data) {
  const response = await fetchWithAuth('/api/admin/settings/gfs', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to save GFS settings');
  }
  return response.json();
}

// Admin: notification settings. GET returns "********" for secrets that are
// already set (never the real value) - same convention as fetchLdapSettings.
export async function fetchAdminNotifySettings() {
  const response = await fetchWithAuth('/api/admin/settings/notify');
  if (!response.ok) throw new Error('Failed to fetch notify settings');
  return response.json();
}

export async function updateNotifySettings(data) {
  const response = await fetchWithAuth('/api/admin/settings/notify', {
    method: 'PUT',
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to save notify settings');
  }
  return response.json();
}

// Sends one real test email right now via the currently-saved SMTP relay
// settings (not necessarily saved ones from a prior session - the caller
// should save first so the test reflects what's actually stored).
export async function sendTestEmail(to) {
  const response = await fetchWithAuth('/api/admin/settings/notify/test-email', {
    method: 'POST',
    body: JSON.stringify({ to }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to send test email');
  }
  return response.json();
}

// Admin: source host / container list management. Deleting a host only
// removes the pull configuration - it never touches ZFS datasets/snapshots.
export async function fetchAdminHosts() {
  const response = await fetchWithAuth('/api/admin/hosts');
  if (!response.ok) throw new Error('Failed to fetch hosts');
  return response.json();
}

export async function createHost(host, containers) {
  const response = await fetchWithAuth('/api/admin/hosts', {
    method: 'POST',
    body: JSON.stringify({ host, containers }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to add host');
  }
  return response.json();
}

export async function deleteHost(host) {
  const response = await fetchWithAuth(`/api/admin/hosts/${encodeURIComponent(host)}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to delete host');
  }
  return response.json();
}

export async function updateHostContainers(host, containers) {
  const response = await fetchWithAuth(`/api/admin/hosts/${encodeURIComponent(host)}/containers`, {
    method: 'PUT',
    body: JSON.stringify({ containers }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to save container list');
  }
  return response.json();
}

export async function updateHostEmails(host, emails) {
  const response = await fetchWithAuth(`/api/admin/hosts/${encodeURIComponent(host)}/emails`, {
    method: 'PUT',
    body: JSON.stringify({ emails }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to save email recipients');
  }
  return response.json();
}

export async function updateHostTimer(host, enabled) {
  const response = await fetchWithAuth(`/api/admin/hosts/${encodeURIComponent(host)}/timer`, {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to update timer');
  }
  return response.json();
}

// Starts a pull for this host immediately instead of waiting for its timer.
// Returns as soon as the pull is queued, not once it's finished.
export async function triggerHostPull(host) {
  const response = await fetchWithAuth(`/api/admin/hosts/${encodeURIComponent(host)}/trigger-pull`, {
    method: 'POST',
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to start pull');
  }
  return response.json();
}

// Starts a GFS prune run immediately instead of waiting for its daily
// 04:00 timer. Returns as soon as it's queued, not once it's finished.
export async function triggerPruneNow() {
  const response = await fetchWithAuth('/api/admin/prune/trigger-now', {
    method: 'POST',
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to start prune');
  }
  return response.json();
}

// The vault's own public SSH key - not a secret, just admin-gated for
// consistency with the rest of the source-host management endpoints.
export async function fetchVaultPublicKey() {
  const response = await fetchWithAuth('/api/admin/vault-key');
  if (!response.ok) throw new Error('Failed to fetch vault public key');
  return response.json();
}

// Tests SSH reachability of a source host. Takes a bare hostname (not tied
// to an existing config entry) so it works both for a host being typed
// into the add-host form and for one already configured.
export async function testHostConnection(host) {
  const response = await fetchWithAuth('/api/admin/hosts/test-connection', {
    method: 'POST',
    body: JSON.stringify({ host }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to test connection');
  }
  return response.json();
}

// Detailed pull-failure log (systemd journal excerpt) for one container.
export async function fetchContainerLog(host, container) {
  const response = await fetchWithAuth(
    `/api/hosts/${encodeURIComponent(host)}/containers/${encodeURIComponent(container)}/log`
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to fetch log');
  }
  return response.json();
}

// Who has downloaded/browsed actual container data, and when - newest first.
export async function fetchAuditLog(offset = 0, limit = 100) {
  const params = new URLSearchParams({ offset, limit });
  const response = await fetchWithAuth(`/api/admin/audit-log?${params}`);
  if (!response.ok) throw new Error('Failed to fetch audit log');
  return response.json();
}

// Every available snapshot for one container, newest first.
export async function fetchContainerSnapshots(host, container) {
  const response = await fetchWithAuth(
    `/api/hosts/${encodeURIComponent(host)}/containers/${encodeURIComponent(container)}/snapshots`
  );
  if (!response.ok) throw new Error('Failed to fetch snapshots');
  return response.json();
}

// A native browser download can't attach an Authorization header, so the
// JWT travels as a query param here instead - the one endpoint on the
// backend that accepts that (see auth_routes.get_current_admin_from_query_token).
// This lets the browser stream a potentially very large file straight to
// disk with its own native download UI, instead of buffering the whole
// thing in page memory first (containers have been seen at 13GB+).
export function buildContainerDownloadUrl(host, container, { snapshot, compression }) {
  const params = new URLSearchParams({ token: getToken(), compression });
  if (snapshot) params.set('snapshot', snapshot);
  return `${API_URL}/api/admin/hosts/${encodeURIComponent(host)}/containers/${encodeURIComponent(container)}/download?${params}`;
}

// Lists one directory inside a chosen snapshot - the read-only file browser,
// for recovering a single file without downloading the whole container or
// needing shell access to the vault host.
export async function browseSnapshot(host, container, { snapshot, path = '', offset = 0, limit = 500 }) {
  const params = new URLSearchParams({ path, offset, limit });
  if (snapshot) params.set('snapshot', snapshot);
  const response = await fetchWithAuth(
    `/api/admin/hosts/${encodeURIComponent(host)}/containers/${encodeURIComponent(container)}/browse?${params}`
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'Failed to browse snapshot');
  }
  return response.json();
}

// Same query-token pattern as buildContainerDownloadUrl - a single file
// out of a chosen snapshot, native browser download.
export function buildFileDownloadUrl(host, container, { snapshot, path }) {
  const params = new URLSearchParams({ token: getToken(), path });
  if (snapshot) params.set('snapshot', snapshot);
  return `${API_URL}/api/admin/hosts/${encodeURIComponent(host)}/containers/${encodeURIComponent(container)}/browse-download?${params}`;
}
