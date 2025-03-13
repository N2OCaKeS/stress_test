#!/bin/bash
echo "Updating password for user u to use MD5 encryption..."
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER USER u WITH ENCRYPTED PASSWORD '1';"
