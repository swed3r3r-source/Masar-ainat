#!/bin/bash
# ينشئ دور التطبيق منفصلًا عن دور الترحيل — التطبيق لا يملك تعديل المخطط.
# يُنفَّذ مرة واحدة عند تهيئة العنقود.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${MASAR_DB_USER}') THEN
    CREATE ROLE ${MASAR_DB_USER} LOGIN PASSWORD '${MASAR_DB_PASSWORD}';
  END IF;
END \$\$;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;
SQL
