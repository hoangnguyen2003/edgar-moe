#!/usr/bin/env bash
# Secret-free setup, shared by the disposable CI and approved provider rehearsal.
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != "true" || "${RUNNER_OS:-}" != "Linux" ]]; then
  echo "PostgreSQL client setup is restricted to the Linux Actions runner." >&2
  exit 1
fi
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
sudo apt-get update
sudo apt-get install --yes --no-install-recommends postgresql-client-18
client_dir=/usr/lib/postgresql/18/bin
for client in psql pg_dump pg_restore; do
  version="$("$client_dir/$client" --version)"
  if [[ "$version" != "$client (PostgreSQL) 18."* ]]; then
    echo "PostgreSQL 18 client verification failed." >&2
    exit 1
  fi
done
echo "$client_dir" >> "$GITHUB_PATH"
echo "PostgreSQL 18 client tools verified."
