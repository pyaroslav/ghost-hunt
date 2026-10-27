#!/usr/bin/env bash
# Create the two accounts Ghost Hunt uses, in a lab CDB running in Docker.
#   lab/users.sh <container> <ORACLE_SID>
# Passwords come from GHOST_HUNTER_PASSWORD and GHOST_EXORCIST_PASSWORD (see ~/.ghost-hunt.env).
set -euo pipefail
docker exec -i -e ORACLE_SID="$2" "$1" bash -lc 'sqlplus -s / as sysdba' <<SQL
whenever sqlerror continue
create user c##ghost identified by "${GHOST_HUNTER_PASSWORD}" container=all;
alter user c##ghost identified by "${GHOST_HUNTER_PASSWORD}" container=all;
grant create session, select any dictionary to c##ghost container=all;
alter user c##ghost set container_data=all container=current;
create user c##exorcist identified by "${GHOST_EXORCIST_PASSWORD}" container=all;
alter user c##exorcist identified by "${GHOST_EXORCIST_PASSWORD}" container=all;
grant create session, select any dictionary, alter system, force any transaction to c##exorcist container=all;
grant execute on sys.dbms_transaction to c##exorcist container=all;
-- purge_lost_db_entry deletes the leftover rows itself, as the caller
grant delete on sys.pending_trans$ to c##exorcist container=all;
grant delete on sys.pending_sessions$ to c##exorcist container=all;
grant delete on sys.pending_sub_sessions$ to c##exorcist container=all;
SQL
