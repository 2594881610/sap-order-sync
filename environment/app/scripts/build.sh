#!/usr/bin/env bash
set -euo pipefail
cd /app
mvn -o -B -DskipTests package
