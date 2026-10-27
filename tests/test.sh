#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
# 先重编译，避免用旧jar或预先伪造的HTTP进程通过验收。
cd /app
# 清除旧编译产物，避免源码时间戳导致Maven沿用旧类文件。
rm -rf /app/target
mvn -o -B -DskipTests package > /logs/verifier/build.log 2>&1
python3 -I "$(dirname "$0")/test_outputs.py"
