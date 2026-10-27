#!/usr/bin/env bash
set -euo pipefail
solution_dir="$(cd "$(dirname "$0")" && pwd)"
export MYSQL_PWD="${DB_PASSWORD:-local_bench_demo}"
mysql --protocol=TCP -h "${DB_HOST:-db}" -P "${DB_PORT:-3306}" \
  -u "${DB_USER:-bench}" "${DB_NAME:-order_bench}" < "$solution_dir/001-order-unique.sql"
cp "$solution_dir/001-order-unique.sql" /app/scripts/001-order-unique.sql
cat > /app/scripts/migrate.sh <<'MIGRATE'
#!/usr/bin/env bash
set -euo pipefail
export MYSQL_PWD="${DB_PASSWORD:-local_bench_demo}"
mysql --protocol=TCP -h "${DB_HOST:-db}" -P "${DB_PORT:-3306}" \
  -u "${DB_USER:-bench}" "${DB_NAME:-order_bench}" < /app/scripts/001-order-unique.sql
MIGRATE
chmod +x /app/scripts/migrate.sh
cp "$solution_dir/OrderService.java" /app/src/main/java/bench/orders/OrderService.java
/app/scripts/build.sh
