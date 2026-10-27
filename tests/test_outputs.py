#!/usr/bin/env python3
"""通过独立HTTP客户端和数据库查询验收订单同步，不依赖应用返回的统计结果。"""
import concurrent.futures
import decimal
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

APP = Path('/app')
LOGS = Path('/logs/verifier')
PORTS = (18080, 18081)
PREFIX = 'VERIFY-' + uuid.uuid4().hex[:12] + '-'
REPORT = {'checks': [], 'race_groups': [], 'duplicate_observed': False}
PROCESSES = {}
HANDLES = []


def sql(statement):
    """使用独立MySQL客户端查询真实数据库；连接失败直接使当前检查失败。"""
    env = dict(os.environ, MYSQL_PWD=os.getenv('DB_PASSWORD', 'local_bench_demo'))
    result = subprocess.run([
        'mysql', '--protocol=TCP', '--batch', '--raw', '--skip-column-names',
        '-h', os.getenv('DB_HOST', 'db'), '-P', os.getenv('DB_PORT', '3306'),
        '-u', os.getenv('DB_USER', 'bench'), os.getenv('DB_NAME', 'order_bench'),
        '-e', statement,
    ], env=env, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('Database check failed: ' + result.stderr[-2000:])
    return [line.split('\t') for line in result.stdout.splitlines()]


def literal(value):
    """将测试字符串转换为UTF-8十六进制SQL字面量，避免依赖转义模式。"""
    return "CONVERT(X'" + value.encode().hex() + "' USING utf8mb4)"


def rows(order_no):
    """按订单号读取主键、物料与精确十进制数量。"""
    return sql('SELECT id, HEX(item_code), qty FROM orders WHERE order_no = '
               + literal(order_no) + ' ORDER BY id')


def post(payload, port=18080):
    """保留HTTP错误响应，供校验明确区分400、500和网络故障。"""
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/orders/sync',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors='replace')


def require_id(response):
    """成功请求必须给出可与数据库主键核对的正整数ID。"""
    status, body = response
    assert status == 200, f'Expected HTTP 200, got {status}: {body}'
    assert isinstance(body, dict) and type(body.get('id')) is int and body['id'] > 0, body
    return body['id']


def expect_order(order_no, order_id, item_code, qty):
    """核对唯一记录、稳定主键和实际存储字段，避免仅靠固定响应通过。"""
    actual = rows(order_no)
    assert len(actual) == 1, f'{order_no}: expected one row, got {len(actual)}'
    assert int(actual[0][0]) == order_id, (actual, order_id)
    assert actual[0][1] == item_code.encode().hex().upper(), actual
    assert decimal.Decimal(actual[0][2]) == decimal.Decimal(str(qty)), actual


def stop(port):
    """仅停止本评分进程启动的JVM，避免误杀其他服务。"""
    process = PROCESSES.pop(port, None)
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def start(port):
    """启动指定端口的独立JVM，健康检查包含数据库连通性。"""
    handle = (LOGS / f'service-{port}.log').open('a')
    HANDLES.append(handle)
    PROCESSES[port] = subprocess.Popen([
        'java', '-Xms64m', '-Xmx256m', '-jar', str(APP / 'target/order-sync.jar'),
        f'--server.port={port}',
    ], cwd=APP, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if PROCESSES[port].poll() is not None:
            raise RuntimeError(f'JVM {port} exited; see service-{port}.log')
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
                if response.status == 200 and json.loads(response.read()).get('status') == 'UP':
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError(f'JVM {port} did not become healthy within 60 seconds')


def check(name, function):
    """逐项记录失败，单个断言失败后继续采集其余验收证据。"""
    try:
        function()
        REPORT['checks'].append({'name': name, 'passed': True})
        print('PASS:', name, flush=True)
    except Exception as error:
        REPORT['checks'].append({'name': name, 'passed': False, 'error': str(error)})
        print('FAIL:', name, str(error), flush=True)


def parallel(payloads):
    """同步释放32个客户端线程，将请求均匀发送到两个独立JVM。"""
    barrier = threading.Barrier(len(payloads))

    def send(index):
        """等待本轮客户端全部就绪后发出一次请求，不在业务代码中制造延迟。"""
        barrier.wait(timeout=15)
        return post(payloads[index], PORTS[index % len(PORTS)])

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return list(pool.map(send, range(len(payloads))))


def run_checks():
    """覆盖串行行为、跨进程竞争、非法请求和重启恢复。"""
    historical = sql('SELECT id, HEX(order_no), HEX(item_code), qty FROM orders ORDER BY id')
    serial_no = PREFIX + 'SERIAL'
    state = {}

    def serial():
        """重复同步保留主键，后续请求可以更新物料和数量。"""
        payload = {'orderNo': serial_no, 'itemCode': 'M-A', 'qty': '12.3456'}
        order_id = require_id(post(payload))
        state['serial_id'] = order_id
        expect_order(serial_no, order_id, 'M-A', '12.3456')
        assert require_id(post(payload, PORTS[1])) == order_id
        updated = dict(payload, itemCode='M-B', qty='999.0001')
        assert require_id(post(updated, PORTS[1])) == order_id
        expect_order(serial_no, order_id, 'M-B', '999.0001')

    check('first_create_retry_and_update', serial)

    def exact_keys():
        """订单号不应自动去空格或转换大小写。"""
        keys = [PREFIX + 'Case', PREFIX + 'case', PREFIX + 'Case ']
        ids = []
        for key in keys:
            order_id = require_id(post({'orderNo': key, 'itemCode': 'M-EXACT', 'qty': '1'}))
            expect_order(key, order_id, 'M-EXACT', '1')
            ids.append(order_id)
        assert len(set(ids)) == len(keys), ids

    check('exact_order_number_comparison', exact_keys)

    def valid_boundaries():
        """数据库字段上限内的合法编码和数量应完整保存。"""
        order_no = PREFIX + 'B' * (64 - len(PREFIX))
        item_code = 'I' * 64
        qty = '99999999999999.9999'
        order_id = require_id(post({'orderNo': order_no, 'itemCode': item_code, 'qty': qty}))
        expect_order(order_no, order_id, item_code, qty)

    check('valid_field_boundaries', valid_boundaries)

    def invalid_requests():
        """拒绝缺字段、空值、超长、负数量、超精度及无法解析的数量。"""
        valid = {'orderNo': PREFIX + 'INVALID', 'itemCode': 'M-I', 'qty': '1'}
        invalid = [{}, None]
        for field in ('orderNo', 'itemCode', 'qty'):
            invalid.append({key: value for key, value in valid.items() if key != field})
            invalid.append(dict(valid, **{field: None}))
        for field in ('orderNo', 'itemCode'):
            invalid.append(dict(valid, **{field: ''}))
            invalid.append(dict(valid, **{field: 'X' * 65}))
        for qty in ('0', '-1', '1.00001', '100000000000000', 'abc'):
            invalid.append(dict(valid, qty=qty))
        before = sql('SELECT COUNT(*) FROM orders')
        for payload in invalid:
            status, body = post(payload)
            assert status == 400, f'{payload}: got {status}: {body}'
        assert sql('SELECT COUNT(*) FROM orders') == before, 'Invalid requests wrote data'

    check('invalid_requests_do_not_write', invalid_requests)

    for group in range(10):
        def same_order(group=group):
            """同一新订单的32个并发重试均须成功，返回同一真实数据库主键。"""
            order_no = PREFIX + f'RACE-{group}'
            payload = {'orderNo': order_no, 'itemCode': f'M-{group}', 'qty': '7.1250'}
            responses = parallel([payload] * 32)
            actual = rows(order_no)
            REPORT['race_groups'].append({'group': group, 'row_count': len(actual),
                                          'http_statuses': [response[0] for response in responses]})
            if len(actual) > 1:
                REPORT['duplicate_observed'] = True
            ids = [require_id(response) for response in responses]
            assert len(actual) == 1, f'Concurrent creation produced {len(actual)} rows'
            assert len(set(ids)) == 1, f'Concurrent responses returned {len(set(ids))} IDs'
            expect_order(order_no, ids[0], payload['itemCode'], payload['qty'])
            assert require_id(post(payload)) == ids[0]
        check(f'concurrent_same_order_{group:02d}', same_order)

    def distinct_orders():
        """不同订单并发写入均应持久化，不能通过丢弃请求满足唯一性。"""
        payloads = [{'orderNo': PREFIX + f'DISTINCT-{index}',
                     'itemCode': f'ITEM-{index}', 'qty': str(index + 1)} for index in range(32)]
        ids = [require_id(response) for response in parallel(payloads)]
        assert len(set(ids)) == 32, ids
        for payload, order_id in zip(payloads, ids):
            expect_order(payload['orderNo'], order_id, payload['itemCode'], payload['qty'])

    check('concurrent_distinct_orders', distinct_orders)

    def restart_retry():
        """全部服务退出后重启，原订单主键和字段仍然保持。"""
        order_id = state['serial_id']
        for port in PORTS:
            stop(port)
        for port in PORTS:
            start(port)
        assert require_id(post({'orderNo': serial_no, 'itemCode': 'M-B', 'qty': '999.0001'})) == order_id
        expect_order(serial_no, order_id, 'M-B', '999.0001')

    check('restart_and_retry', restart_retry)

    def preserve_history():
        """测试前已有记录须原样保留，不允许清空表掩盖问题。"""
        current = {row[0]: row for row in sql('SELECT id, HEX(order_no), HEX(item_code), qty FROM orders')}
        for row in historical:
            assert current.get(row[0]) == row, f'Historical row changed: {row[0]}'

    check('historical_rows_preserved', preserve_history)

    def repeat_migration():
        """迁移可重复运行，不能改变已写入的订单记录。"""
        script = APP / 'scripts/migrate.sh'
        assert script.is_file(), 'Required /app/scripts/migrate.sh is missing'
        before = sql('SELECT id, HEX(order_no), HEX(item_code), qty FROM orders ORDER BY id')
        for _ in range(2):
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True, timeout=20)
            assert result.returncode == 0, result.stderr[-2000:]
        assert sql('SELECT id, HEX(order_no), HEX(item_code), qty FROM orders ORDER BY id') == before

    check('migration_is_repeatable', repeat_migration)


def main():
    """任何未处理异常都保留零分，并清理本次评分启动的两个进程。"""
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / 'reward.txt').write_text('0\n')
    try:
        for port in PORTS:
            start(port)
        run_checks()
        REPORT['passed'] = all(check['passed'] for check in REPORT['checks'])
    except Exception as error:
        REPORT['passed'] = False
        REPORT['fatal_error'] = str(error)
        print('ERROR:', error, flush=True)
    finally:
        for port in PORTS:
            stop(port)
        for handle in HANDLES:
            handle.close()
        (LOGS / 'report.json').write_text(json.dumps(REPORT, indent=2, ensure_ascii=False) + '\n')
    if REPORT.get('passed'):
        (LOGS / 'reward.txt').write_text('1\n')
    return 0 if REPORT.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
