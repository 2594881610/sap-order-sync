#!/usr/bin/env python3
"""使用独立Compose项目验证三次故障版、三次参考修复，并保存实际运行证据。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / 'environment/docker-compose.yaml'
ARTIFACTS = ROOT / 'artifacts'


def command(args, log, *, check=True, timeout=900):
    """将命令输出写入当前场景日志，失败时保留完整日志供排查。"""
    log.write('$ ' + ' '.join(str(arg) for arg in args) + '\n')
    log.flush()
    result = subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}): {args}')
    return result.returncode


def trial(kind, number):
    """每个场景创建全新数据库，场景结束仅清理本脚本创建的容器和卷。"""
    project = f'order-bench-{kind}-{number}-{uuid.uuid4().hex[:8]}'
    output = ARTIFACTS / f'{kind}-{number}'
    output.mkdir(parents=True, exist_ok=True)
    compose = ['docker', 'compose', '-p', project, '-f', str(COMPOSE)]
    result = {'scenario': kind, 'number': number, 'project': project}
    with (output / 'execution.log').open('w') as log:
        try:
            command(compose + ['up', '-d', '--no-build', '--wait', '--wait-timeout', '180'], log)
            # 仅复制当前源码和脚本，避免带入本机target或缓存的旧源码。
            for name in ('pom.xml', 'src', 'scripts'):
                command(compose + ['cp', str(ROOT / 'environment/app' / name), 'main:/app/'], log)
            command(compose + ['cp', str(ROOT / 'tests'), 'main:/tests'], log)
            if kind == 'oracle':
                command(compose + ['cp', str(ROOT / 'solution'), 'main:/solution'], log)
                command(compose + ['exec', '-T', 'main', 'bash', '/solution/solve.sh'], log)
                # 第二次完整执行参考解同时检查迁移及修复脚本可重复运行。
                command(compose + ['exec', '-T', 'main', 'bash', '/solution/solve.sh'], log)
            code = command(compose + ['exec', '-T', 'main', 'bash', '/tests/test.sh'], log,
                           check=False, timeout=300)
            command(compose + ['cp', 'main:/logs/verifier/.', str(output / 'verifier')], log)
            reward = (output / 'verifier/reward.txt').read_text().strip()
            report = json.loads((output / 'verifier/report.json').read_text())
            result.update(exit_code=code, reward=reward,
                          duplicate_observed=report.get('duplicate_observed', False))
            if kind == 'baseline':
                assert code != 0 and reward == '0' and report['duplicate_observed'], result
                # 反向验证必须确实暴露重复，而不是仅因构建或启动失败得零分。
                assert 'fatal_error' not in report, report
                allowed = ('concurrent_', 'migration_is_repeatable')
                unexpected = [entry for entry in report['checks']
                              if not entry['passed'] and not entry['name'].startswith(allowed)]
                assert not unexpected, unexpected
            else:
                assert code == 0 and reward == '1' and report['passed'], result
                verify_migration_guard(compose, log)
                verify_zero_on_build_failure(compose, output, log)
            result['passed'] = True
        except Exception as error:
            result.update(passed=False, error=str(error))
        finally:
            command(compose + ['logs', '--no-color'], log, check=False, timeout=30)
            command(compose + ['down', '-v', '--remove-orphans'], log, check=False, timeout=60)
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def verify_migration_guard(compose, log):
    """在三个隔离库分别验证订单、计划和工序重复均被拒绝且不改原数据。"""
    for table, index, insert_sql in (
        ('orders', 'uk_orders_order_no',
         "INSERT INTO orders(order_no,item_code,qty) VALUES ('DUP','M-A',1),('DUP','M-B',2)"),
        ('production_plans', 'uk_plan_order',
         "INSERT INTO orders(id,order_no,item_code,qty) VALUES (1,'ONE','M-A',1); "
         "INSERT INTO production_plans(order_id,item_code,qty,status) VALUES (1,'M-A',1,'WAITING'),(1,'M-B',2,'WAITING')"),
        ('process_details', 'uk_detail_step',
         "INSERT INTO orders(id,order_no,item_code,qty) VALUES (1,'ONE','M-A',1); "
         "INSERT INTO production_plans(id,order_id,item_code,qty,status) VALUES (1,1,'M-A',1,'WAITING'); "
         "INSERT INTO process_details(plan_id,step_no,process_code,qty) VALUES (1,1,'CUT',1),(1,1,'CUT',2)"),
    ):
        database = 'probe_' + table
        setup = f"CREATE DATABASE {database}; GRANT ALL ON {database}.* TO 'bench'@'%'; USE {database}; "
        for name in ('orders', 'production_plans', 'process_details'):
            setup += f'CREATE TABLE {name} LIKE order_bench.{name}; '
        setup += f'ALTER TABLE {table} DROP INDEX {index}; ' + insert_sql + ';'
        command(compose + ['exec', '-T', '-e', 'MYSQL_PWD=local_root_demo', 'db',
                           'mysql', '-u', 'root', '-e', setup], log)
        def read_rows():
            args = compose + ['exec', '-T', '-e', 'MYSQL_PWD=local_root_demo', 'db',
                              'mysql', '-u', 'root', '-N', '-B', '-e',
                              f'SELECT * FROM {database}.{table} ORDER BY id']
            result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
            return result.stdout
        before = read_rows()
        code = command(compose + ['exec', '-T', '-e', f'DB_NAME={database}', 'main',
                                  'bash', '/app/scripts/migrate.sh'], log, check=False)
        assert code != 0, f'Migration accepted duplicates in {table}'
        assert read_rows() == before, f'Migration changed rows in {table}'
        log.write(f'PASS: migration rejects duplicates in {table} without data loss\n')


def verify_zero_on_build_failure(compose, output, log):
    """主动制造编译失败，确认评分入口先清除旧满分再退出。"""
    args = compose + ['exec', '-T', 'main', 'bash', '-c',
                      "printf '1\\n' > /logs/verifier/reward.txt; "
                      "printf 'invalid java\\n' > /app/src/main/java/bench/orders/OrderApplication.java; "
                      'bash /tests/test.sh']
    code = command(args, log, check=False)
    assert code != 0, 'Expected intentional compilation failure'
    command(compose + ['cp', 'main:/logs/verifier/reward.txt',
                       str(output / 'build-failure-reward.txt')], log)
    assert (output / 'build-failure-reward.txt').read_text().strip() == '0'
    log.write('PASS: compilation failure overwrites old reward with zero\n')


def main():
    """构建一次镜像，再逐一运行隔离场景，失败时返回非零退出码。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--reuse-images', action='store_true',
                        help='复用已构建镜像；仅适用于pom.xml和Docker环境未改变时')
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error('--rounds must be positive')
    # 平台禁止作者提供的Compose包含变量插值，启动验证前先检查原文件。
    if '$' in COMPOSE.read_text():
        parser.error('平台禁止Compose包含美元符号，请使用字面量路径和配置值')
    ARTIFACTS.mkdir(exist_ok=True)
    with (ARTIFACTS / 'build.log').open('w') as log:
        if args.reuse_images:
            for image in ('sap-order-sync-main:local', 'sap-order-sync-db:local'):
                command(['docker', 'image', 'inspect', image], log)
        else:
            command(['docker', 'compose', '-p', 'order-bench-build', '-f', str(COMPOSE),
                     'build', 'main', 'db'], log)
    started = time.time()
    results = []
    for number in range(1, args.rounds + 1):
        for kind in ('baseline', 'oracle'):
            results.append(trial(kind, number))
            if not results[-1]['passed']:
                break
        if not results[-1]['passed']:
            break
    summary = {'passed': len(results) == args.rounds * 2 and all(row['passed'] for row in results),
               'elapsed_seconds': round(time.time() - started, 1), 'results': results}
    (ARTIFACTS / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
