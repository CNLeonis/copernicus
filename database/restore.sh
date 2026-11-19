#!/bin/sh
set -eu

pg_restore --list /snapshot/opole.dump >/dev/null
backup="/backups/before-restore-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
pg_dump --format=custom --no-owner --no-acl --file="$backup"
echo "Backup saved: database/backups/$(basename "$backup")"

pg_restore --dbname="$PGDATABASE" --clean --if-exists --no-owner --no-acl \
    --exit-on-error --single-transaction /snapshot/opole.dump
echo "Shared Opole database restored."
