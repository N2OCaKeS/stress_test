# entrypoint.sh
until pg_isready -h postgres -p 5432 -U u; do
    echo "Waiting for PostgreSQL to start..."
    sleep 2
done