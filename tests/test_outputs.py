#!/usr/bin/env python3
"""通过独立HTTP客户端和数据库查询验收订单同步，不依赖应用返回的统计结果。"""
import concurrent.futures
from contextlib import contextmanager
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


def post(payload, port=18080, path="/api/orders/sync"):
    """保留HTTP错误响应，供校验明确区分400、500和网络故障。"""
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}{path}',
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


def parallel(payloads, paths=None):
    """同步释放32个客户端线程，将请求均匀发送到两个独立JVM。"""
    barrier = threading.Barrier(len(payloads))

    def send(index):
        """等待本轮客户端全部就绪后发出一次请求，不在业务代码中制造延迟。"""
        barrier.wait(timeout=15)
        return post(payloads[index], PORTS[index % len(PORTS)], paths[index] if paths else "/api/orders/sync")

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return list(pool.map(send, range(len(payloads))))



def payload(label, item='M-A', qty='7.1250'):
    """生成独立订单，避免不同检查互相污染。"""
    return {'orderNo': PREFIX + label, 'itemCode': item, 'qty': qty}


def batch(entries, auto=False, port=18080):
    """提交批量同步。"""
    return post({'orders': entries, 'autoSchedule': auto}, port, '/api/orders/sync-batch')


def schedule(ids, port=18080):
    """提交批量手工排产。"""
    return post({'planIds': ids}, port, '/api/plans/schedule')


def successful(response, key, expected=None):
    """验证成功响应及列表顺序。"""
    status, body = response
    assert status == 200 and isinstance(body, dict), response
    values = body.get(key)
    assert isinstance(values, list) and all(type(v) is int and v > 0 for v in values), body
    if expected is not None:
        assert values == expected, (values, expected)
    return values


def plan_for(order_id):
    """读取唯一计划与其业务字段。"""
    found = sql(f'SELECT id,item_code,qty,status FROM production_plans WHERE order_id={order_id} ORDER BY id')
    assert len(found) == 1, found
    return found[0]


def details(plan_id):
    """包含主键，检查重试不能删除重建工序。"""
    return sql(f'SELECT id,step_no,process_code,qty FROM process_details WHERE plan_id={plan_id} ORDER BY step_no,id')


def expect_details(plan_id, item, qty):
    """按公开路线检查工序数量、顺序和排产数量快照。"""
    actual = details(plan_id)
    codes = {'M-A': ['CUT', 'PACK'], 'M-B': ['CUT', 'DRILL', 'PACK']}[item]
    assert len(actual) == len(codes), actual
    assert [r[1] for r in actual] == [str(i + 1) for i in range(len(codes))], actual
    assert [r[2] for r in actual] == codes, actual
    assert all(decimal.Decimal(r[3]) == decimal.Decimal(qty) for r in actual), actual
    return actual


def snapshot():
    """使用完整三表快照检查失败事务和迁移是否改变业务数据。"""
    return [sql(f'SELECT * FROM {table} ORDER BY id')
            for table in ('orders', 'production_plans', 'process_details')]



@contextmanager
def locked_transaction(statement):
    """用独立数据库会话控制锁的释放，业务代码不包含测试延迟。"""
    path = LOGS / ('lock-session-' + uuid.uuid4().hex + '.log')
    env = dict(os.environ, MYSQL_PWD=os.getenv('DB_PASSWORD', 'local_bench_demo'))
    with path.open('w') as handle:
        process = subprocess.Popen([
            'mysql', '--protocol=TCP', '--batch', '--raw', '--skip-column-names', '--unbuffered',
            '-h', os.getenv('DB_HOST', 'db'), '-P', os.getenv('DB_PORT', '3306'),
            '-u', os.getenv('DB_USER', 'bench'), os.getenv('DB_NAME', 'order_bench'),
        ], stdin=subprocess.PIPE, stdout=handle, stderr=subprocess.STDOUT, text=True, env=env)
        committed = False
        try:
            process.stdin.write('START TRANSACTION;\n' + statement + ";\nSELECT 'LOCK_READY';\n")
            process.stdin.flush()
            deadline = time.monotonic() + 10
            while 'LOCK_READY' not in path.read_text():
                assert process.poll() is None, path.read_text()
                assert time.monotonic() < deadline, 'Database lock setup timed out'
                time.sleep(0.05)
            yield
            process.stdin.write('COMMIT;\n')
            process.stdin.close()
            process.wait(timeout=5)
            assert process.returncode == 0, path.read_text()
            committed = True
        finally:
            if not committed and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def run_checks():
    """验收多入口竞争、保护字段、整批回滚和真实持久化。"""
    historical = snapshot()
    state = {}

    def serial():
        entry = payload('SERIAL')
        oid = require_id(post(entry))
        state.update(entry=entry, oid=oid)
        assert require_id(post(entry, PORTS[1])) == oid
        plan = plan_for(oid)
        assert plan[1:] == ['M-A', '7.1250', 'WAITING'], plan
        assert not details(plan[0])
        changed = dict(entry, itemCode='M-B', qty='9.2500')
        assert require_id(post(changed)) == oid
        expect_order(entry['orderNo'], oid, 'M-B', '9.2500')
        assert plan_for(oid) == [plan[0], 'M-B', '9.2500', 'WAITING']
        successful(schedule([int(plan[0])]), 'planIds', [int(plan[0])])
        before = expect_details(plan[0], 'M-B', '9.2500')
        successful(schedule([int(plan[0])], PORTS[1]), 'planIds', [int(plan[0])])
        assert details(plan[0]) == before
        changed = dict(entry, itemCode='M-A', qty='11.0001')
        successful(batch([changed], True), 'ids', [oid])
        expect_order(entry['orderNo'], oid, 'M-A', '11.0001')
        assert plan_for(oid) == [plan[0], 'M-B', '11.0001', 'SCHEDULED']
        assert details(plan[0]) == before
        state['snapshot'] = details(plan[0])
    check('serial_sync_schedule_and_protected_fields', serial)

    def exact_keys():
        ids = []
        for key in ('Case', 'case', 'Case '):
            entry = payload(key)
            oid = require_id(post(entry))
            expect_order(entry['orderNo'], oid, 'M-A', '7.1250')
            plan_for(oid)
            ids.append(oid)
        assert len(set(ids)) == 3, ids
        entry = {'orderNo': PREFIX + 'X' * (64-len(PREFIX)), 'itemCode': 'I'*64, 'qty': '99999999999999.9999'}
        oid = require_id(post(entry))
        expect_order(entry['orderNo'], oid, entry['itemCode'], entry['qty'])
    check('exact_keys_and_boundaries', exact_keys)

    def invalid():
        entry = payload('INVALID')
        before = snapshot()
        bad = [dict(entry, **{field: value}) for field, values in (
            ('orderNo', [None, '', 'x'*65]), ('itemCode', [None, '', 'x'*65]),
            ('qty', [None, 0, -1, '0.00001', '100000000000000', 'abc'])) for value in values]
        bad += [{k:v for k,v in entry.items() if k != field} for field in entry]
        for value in bad:
            assert post(value)[0] == 400, value
        requests = [({}, '/api/orders/sync-batch'), ({'orders': []}, '/api/orders/sync-batch'),
                    ({'orders':[None]}, '/api/orders/sync-batch'),
                    ({'orders':[entry,entry]}, '/api/orders/sync-batch'),
                    ({'orders':[entry]*33}, '/api/orders/sync-batch'),
                    ({'planIds':[]}, '/api/plans/schedule'),
                    ({'planIds':[None]}, '/api/plans/schedule'),
                    ({'planIds':[0]}, '/api/plans/schedule'),
                    ({'planIds':[1,1]}, '/api/plans/schedule')]
        for value, path in requests:
            response = post(value, path=path)
            assert response[0] == 400, (value,response)
        assert snapshot() == before
    check('invalid_inputs_leave_no_writes', invalid)

    def rollback():
        valid = payload('ROLLBACK-A', qty='2')
        invalid_route = payload('ROLLBACK-Z', 'NO-ROUTE')
        before = snapshot()
        assert batch([valid,invalid_route], True)[0] == 409
        assert snapshot() == before, '自动排产失败留下了部分订单、计划或工序'
        ids = successful(batch([valid,invalid_route]), 'ids')
        plans = [int(plan_for(i)[0]) for i in ids]
        before = snapshot()
        assert schedule(plans)[0] == 409
        assert snapshot() == before, '手工排产失败留下了部分工序'
        assert schedule([plans[0], 9223372036854775807])[0] == 409
        assert snapshot() == before
        changed = dict(valid, qty='99')
        assert batch([changed, {'orderNo':'DEMO-COMPLETED-001','itemCode':'M-A','qty':'3'}])[0] == 409
        assert snapshot() == before, '已完成计划导致失败时没有整批回滚'
        assert schedule([2])[0] == 409
        assert snapshot() == before
    check('business_conflicts_roll_back_whole_batch', rollback)

    # 触发真实数据库写入故障，验证不是只预检查已知业务错误。
    def database_failure():
        entry = payload('WRITE-FAIL')
        oid = require_id(post(entry))
        pid = int(plan_for(oid)[0])
        before = snapshot()
        sql(f"ALTER TABLE process_details ADD CONSTRAINT verifier_detail_failure CHECK(plan_id <> {pid} OR step_no <> 2)")
        try:
            response = schedule([pid])
            assert 400 <= response[0] < 600, response
            assert snapshot() == before, '第二道工序写入失败后留下部分结果'
        finally:
            sql('ALTER TABLE process_details DROP CHECK verifier_detail_failure')
        successful(schedule([pid]), 'planIds', [pid])
        expect_details(pid, 'M-A', '7.1250')
    check('database_failure_rolls_back_then_retry', database_failure)

    for group in range(10):
        def same_order(group=group):
            entry = payload(f'RACE-{group}')
            responses = parallel([entry]*32)
            actual = rows(entry['orderNo'])
            REPORT['race_groups'].append({'group':group,'row_count':len(actual),
                                          'statuses':[r[0] for r in responses]})
            if len(actual) > 1:
                REPORT['duplicate_observed'] = True
            ids = [require_id(r) for r in responses]
            assert len(set(ids)) == 1, ids
            expect_order(entry['orderNo'], ids[0], 'M-A', '7.1250')
            assert plan_for(ids[0])[3] == 'WAITING'
        check(f'concurrent_same_order_{group}', same_order)

    for group in range(6):
        def competing_schedule(group=group):
            entry = payload(f'SCHEDULE-{group}')
            oid = require_id(post(entry))
            pid = int(plan_for(oid)[0])
            calls = [{'planIds':[pid]} if i%2 else {'orders':[entry], 'autoSchedule':True} for i in range(32)]
            paths = ['/api/plans/schedule' if i%2 else '/api/orders/sync-batch' for i in range(32)]
            responses = parallel(calls, paths)
            actual = details(pid)
            if len(actual) > 2:
                REPORT['duplicate_observed'] = True
            for i,response in enumerate(responses):
                successful(response, 'planIds' if i%2 else 'ids', [pid] if i%2 else [oid])
            assert plan_for(oid)[3] == 'SCHEDULED'
            before = expect_details(pid, 'M-A', '7.1250')
            successful(schedule([pid]), 'planIds', [pid])
            assert details(pid) == before
        check(f'concurrent_auto_and_manual_schedule_{group}', competing_schedule)

    for group in range(4):
        def reverse_batches(group=group):
            entries = [payload(f'OVERLAP-{group}-{i}', 'M-B', str(i+1)) for i in range(3)]
            ids = successful(batch(entries), 'ids')
            pids = [int(plan_for(i)[0]) for i in ids]
            calls, paths = [], []
            for i in range(16):
                if i%2:
                    calls.append({'planIds':pids[::-1]});paths.append('/api/plans/schedule')
                else:
                    calls.append({'orders':entries,'autoSchedule':True});paths.append('/api/orders/sync-batch')
            responses = parallel(calls, paths)
            for i,r in enumerate(responses):
                successful(r,'planIds' if i%2 else 'ids', pids[::-1] if i%2 else ids)
            for pid in pids:
                if len(details(pid)) > 3:
                    REPORT['duplicate_observed'] = True
            for i,pid in enumerate(pids):
                expect_details(pid,'M-B',str(i+1))
        check(f'concurrent_reverse_overlapping_batches_{group}', reverse_batches)

    def material_race():
        for group in range(4):
            entry = payload(f'MATERIAL-{group}', qty='3')
            oid = require_id(post(entry));pid = int(plan_for(oid)[0])
            changed = dict(entry, itemCode='M-B', qty='8')
            calls=[{'planIds':[pid]}, {'orders':[changed],'autoSchedule':True}]*8
            paths=['/api/plans/schedule','/api/orders/sync-batch']*8
            responses=parallel(calls,paths)
            for i,r in enumerate(responses):
                successful(r,'planIds' if i%2==0 else 'ids',[pid] if i%2==0 else [oid])
            plan=plan_for(oid)
            expect_order(entry['orderNo'],oid,'M-B','8')
            assert plan[2:] == ['8.0000','SCHEDULED'], plan
            assert plan[1] in ('M-A','M-B'), plan
            # 两种提交顺序都合法；物料和快照必须来自同一次排产。
            expect_details(pid,plan[1],'3' if plan[1]=='M-A' else '8')
    check('concurrent_material_change_preserves_schedule_snapshot', material_race)


    def latest_state_after_wait():
        entry = payload('LOCK-STATE')
        oid = require_id(post(entry)); pid = int(plan_for(oid)[0])
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with locked_transaction(
                f'UPDATE orders SET qty=qty WHERE id={oid}; '
                f"UPDATE production_plans SET status='COMPLETED' WHERE id={pid}"
            ):
                future = pool.submit(schedule, [pid])
                try:
                    response = future.result(timeout=0.5)
                except concurrent.futures.TimeoutError:
                    response = None
                assert response is None, ('请求越过尚未提交的业务锁', response)
            assert future.result(timeout=15)[0] == 409, '没有根据锁释放后的最新状态拒绝排产'
        assert plan_for(oid)[3] == 'COMPLETED'
        assert not details(pid), '已完成计划被过期读取重新排产'
    check('concurrent_state_rechecked_after_lock_wait', latest_state_after_wait)

    def independent_progress():
        blocked, other = payload('BLOCKED'), payload('INDEPENDENT')
        oid = require_id(post(blocked))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            with locked_transaction(f'UPDATE orders SET qty=qty WHERE id={oid}'):
                first = pool.submit(post, blocked, PORTS[0])
                try:
                    response = first.result(timeout=0.5)
                except concurrent.futures.TimeoutError:
                    response = None
                assert response is None, response
                second = pool.submit(post, other, PORTS[0])
                other_id = require_id(second.result(timeout=5))
                expect_order(other['orderNo'], other_id, 'M-A', '7.1250')
            assert require_id(first.result(timeout=15)) == oid
    check('concurrent_unrelated_order_progresses_while_one_is_locked', independent_progress)

    def distinct():
        entries=[payload(f'DISTINCT-{i}') for i in range(32)]
        ids=[require_id(r) for r in parallel(entries)]
        assert len(set(ids)) == 32
        for entry,oid in zip(entries,ids):
            expect_order(entry['orderNo'],oid,'M-A','7.1250');plan_for(oid)
    check('concurrent_distinct_orders',distinct)

    def restart():
        for port in PORTS: stop(port)
        for port in PORTS: start(port)
        entry=dict(state['entry'],qty='11.0001')
        assert require_id(post(entry)) == state['oid']
        pid=plan_for(state['oid'])[0]
        assert details(pid) == state['snapshot']
    check('restart_retains_ids_and_processes',restart)

    def preserve():
        current=snapshot()
        for original,now in zip(historical,current):
            by_id={row[0]:row for row in now}
            assert all(by_id.get(row[0])==row for row in original)
    check('historical_three_tables_preserved',preserve)

    def migration():
        before=snapshot()
        for _ in range(2):
            result=subprocess.run(['bash','/app/scripts/migrate.sh'],capture_output=True,text=True,timeout=20)
            assert result.returncode==0,result.stderr[-2000:]
        assert snapshot()==before
    check('migration_is_repeatable',migration)


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
