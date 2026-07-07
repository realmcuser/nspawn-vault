import React from 'react';

// Custom mark for nspawn-vault - a vault/safe door handle (concentric rings
// + radiating spokes), not a generic lucide icon. Deliberately distinct from
// both the lucide ShieldCheck this replaced and the build tool's Package/Server
// marks. Uses currentColor so it inherits text color like any lucide icon.
const VaultIcon = ({ className }) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    xmlns="http://www.w3.org/2000/svg"
  >
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="3.25" />
    <path d="M12 3v3.5M12 17.5V21M3 12h3.5M17.5 12H21" />
    <path d="M5.8 5.8l2.4 2.4M15.8 15.8l2.4 2.4M5.8 18.2l2.4-2.4M15.8 8.2l2.4-2.4" />
  </svg>
);

export default VaultIcon;
