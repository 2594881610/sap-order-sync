#!/usr/bin/env bash
set -euo pipefail
cd /app
exec java -Xms64m -Xmx256m -jar target/order-sync.jar
