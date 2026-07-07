#!/usr/bin/env python3
# Läser ZFS-snapshotnamn (YYYYMMDD-HHMMSS) från stdin,
# skriver ut de som SKA RADERAS till stdout.
import sys
from datetime import datetime, timedelta, timezone

gh = int(sys.argv[1])  # timmar att behålla (hourly)
gd = int(sys.argv[2])  # dagar  (daily)
gw = int(sys.argv[3])  # veckor (weekly)
gm = int(sys.argv[4])  # månader (monthly)
gy = int(sys.argv[5])  # år     (yearly)

snaps = [s.strip() for s in sys.stdin if s.strip()]
now = datetime.now(timezone.utc)
keep = {}

for s in sorted(snaps, reverse=True):
    try:
        dt = datetime.strptime(s[:15], '%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc)
    except Exception:
        continue
    if gh and dt >= now - timedelta(hours=gh):
        b = 'H' + dt.strftime('%Y%m%d%H')
        if b not in keep: keep[b] = s
    if gd and dt >= now - timedelta(days=gd):
        b = 'D' + dt.strftime('%Y%m%d')
        if b not in keep: keep[b] = s
    if gw and dt >= now - timedelta(weeks=gw):
        b = 'W' + dt.strftime('%G%V')
        if b not in keep: keep[b] = s
    if gm and dt >= now - timedelta(days=gm * 30):
        b = 'M' + dt.strftime('%Y%m')
        if b not in keep: keep[b] = s
    if gy and dt >= now - timedelta(days=gy * 365):
        b = 'Y' + dt.strftime('%Y')
        if b not in keep: keep[b] = s

keepers = set(keep.values())
for s in snaps:
    if s not in keepers:
        print(s)
