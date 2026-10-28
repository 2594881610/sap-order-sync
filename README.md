# 订单同步与排产并发解决方案

这是一个基于 hdjz 订单同步问题精简重写的独立演示，包含有并发缺陷的 Java 服务、隔离 MySQL、参考修复和自动评分。使用订单、生产计划、工序明细三张表和两条演示工艺路线，不依赖 SAP、公司数据库或原 MES 工程。

## 文件与角色

- `environment/app`：解题模型修改的Java项目；仓库交付的是故障版。
- `environment/initdb`：初始表结构与演示记录，由 `environment/Dockerfile.db` 复制进数据库镜像。
- `instruction.md`：模型可见的需求与验收契约。
- `solution`：出题方持有的参考修复，不放进解题镜像。
- `tests`：评分入口和验证逻辑，不放进解题镜像。
- `scripts/validate.py`：本地正反向验证工具。

在 IDEA 中打开 `environment/app/pom.xml` 即可查看独立 Java 工程。主要故障代码在 `environment/app/src/main/java/bench/orders/OrderService.java`；对应参考修复在 `solution/OrderService.java`。保留两份代码的区别，提交题目时应交付故障版。

详细材料来源见 `PROVENANCE.md`。本项目及示例数据经过AI辅助生成，不应填写成原样生产代码或真实业务数据。是否可提交评测平台，需按平台来源规则确认。

## 一次完成三轮验证

先启动 Docker Desktop。在本目录执行：

```bash
python3 scripts/validate.py
```

命令先构建镜像，再创建六个相互独立的Compose环境：三次故障版和三次参考修复。故障版必须实际出现重复订单或重复工序；参考解必须全部通过。日志和JSON报告保存在 `artifacts/`，每个场景结束后只清理本次创建的容器及其数据库卷。

如果镜像已构建，且仅修改了Java 源码或脚本，可使用 `python3 scripts/validate.py --reuse-images` 复用环境；验证器仍会复制当前源码并重新编译。修改 `pom.xml` 或Docker环境后应执行完整构建。

Docker构建使用BuildKit的依赖缓存，Docker Desktop默认支持；完整依赖同时保存在镜像内，运行时不依赖构建缓存。IDE配置和本机编译产物不会进入镜像或提交ZIP。

数据库初始化SQL随MySQL镜像交付，不使用Compose的 `volumes` 或宿主机目录挂载。修改初始化 SQL 后需要重建数据库镜像，并使用全新数据库环境验证。平台曾明确禁止 `volumes`，旧版通过目录挂载初始化SQL的配置已移除。

平台同时禁止作者提供的Compose文件出现美元符号。两份镜像的构建上下文使用相对于Compose文件所在目录的 `.`，不使用环境变量插值；本地验证工具会在启动前检查这一限制。

首次运行需要联网下载镜像及依赖。镜像构建后，Java重新编译使用离线Maven；测试不需要Python第三方库。完整过程需要Docker可访问镜像源和Maven仓库。

## 手动启动故障环境

```bash
docker compose -p order-demo -f environment/docker-compose.yaml up -d --build --wait
```

进入容器：

```bash
docker compose -p order-demo -f environment/docker-compose.yaml exec main bash
```

在容器中启动接口：

```bash
PORT=8080 bash /app/scripts/start.sh
```

另开终端，在本目录直接从容器内访问接口：

```bash
docker compose -p order-demo -f environment/docker-compose.yaml exec main \
  curl -sS http://127.0.0.1:8080/api/orders/sync \
  -H 'Content-Type: application/json' \
  -d '{"orderNo":"TRY-001","itemCode":"M-A","qty":12.3456}'
```

数据库与HTTP端口默认均不映射到本机，示例命令通过 `docker compose exec` 在容器内执行。两个JVM的自动启动和并发请求由评分脚本负责。

查看数据库：

```bash
docker compose -p order-demo -f environment/docker-compose.yaml exec \
  -e MYSQL_PWD=local_bench_demo main \
  mysql -h db -u bench order_bench -e 'SELECT * FROM orders ORDER BY id'
```

查看数据库启动日志：

```bash
docker compose -p order-demo -f environment/docker-compose.yaml logs db
```

结束手动环境：

```bash
docker compose -p order-demo -f environment/docker-compose.yaml down -v
```

最后一条命令会删除 `order-demo` 环境的演示数据库。需要保留该环境数据时不要加 `-v`。

## 手动评分与参考解

评分启动独立端口18080、18081，完成后自动停止自己启动的进程。当前环境运行过故障版并发测试后可能已有重复订单；验证参考解时请另用全新环境，不要自动清理重复数据。

复制测试并执行：

```bash
docker compose -p order-demo -f environment/docker-compose.yaml cp tests main:/tests
docker compose -p order-demo -f environment/docker-compose.yaml exec -T main bash /tests/test.sh
```

故障版这里预期非零退出，并将 `/logs/verifier/reward.txt` 写为0。

在另一套全新环境验证参考解：

```bash
docker compose -p order-fixed -f environment/docker-compose.yaml up -d --build --wait
docker compose -p order-fixed -f environment/docker-compose.yaml cp solution main:/solution
docker compose -p order-fixed -f environment/docker-compose.yaml cp tests main:/tests
docker compose -p order-fixed -f environment/docker-compose.yaml exec -T main bash /solution/solve.sh
docker compose -p order-fixed -f environment/docker-compose.yaml exec -T main bash /tests/test.sh
docker compose -p order-fixed -f environment/docker-compose.yaml exec -T main cat /logs/verifier/reward.txt
```

预期结果为1。完整报告在容器内 `/logs/verifier/report.json`，编译日志、两个服务日志也在同一目录。结束后可用对应项目名执行 `down -v` 清理演示环境。

## Harbor与评分边界

采用 Harbor 任务目录，`task.toml` 的 schema 版本为 1.3，`main` 为解题容器、`db` 为数据库侧车。本地验证直接使用 Docker Compose；平台评测请在 Web IDE 更新完整题目目录后重新执行 Oracle 和候选模型测试。

本地Compose验证不等同于平台验证或模型难度评测。平台能否接收多容器、材料来源是否符合其规则，应以其实际检查结果为准。

评分仅输出 0 或 1。每次重新编译并启动两个 JVM，覆盖：

- 10 组、每组 32 个相同新订单请求，及 32 个不同订单并发。
- 6 组自动与手工排产竞争，每组 32 个请求。
- 4 组反向重叠批次，每组 16 个请求。
- 4 组物料更新与排产竞争，接受合法的两种提交顺序。
- 锁等待后的最新状态复核、一个订单被锁时无关订单仍可处理。
- 业务冲突及实际写入失败的整批回滚、重试、重启、三表历史保护和重复迁移。

应用不包含业务延迟或测试专用接口。写入失败检查临时增加 CHECK 约束，数据库锁测试使用独立事务控制竞争；测试结束会移除临时约束并关闭数据库会话。共享环境评分不构成抵御恶意系统篡改的安全隔离。

## 新版业务与参考修复

单单同步接口保持兼容；新增 `/api/orders/sync-batch` 和 `/api/plans/schedule`，完整契约见 `instruction.md`。

参考解先检查三类历史重复业务键，再添加订单号、计划所属订单、工序所属计划与序号的唯一约束。同步入口按订单号排序，利用原子写入取得订单行锁；手工入口按相同顺序取得订单行锁，再读取计划最新状态。所有业务写入在同一事务中完成。已排产计划保留物料和工序快照，计划数量允许更新。

这是 v2 难度升级；旧版本源码备份在项目旁的 `sap-order-sync-before-scheduling.zip`。提交时仍交付 `environment/app` 中的故障版，不要用参考解覆盖它。

## 提交文件

必需内容为 `task.toml`、`instruction.md`、整个 `environment/`、`solution/`、`tests/`。`README.md`、`PROVENANCE.md`、`VALIDATION.md`、`validation/` 和 `scripts/` 是来源、复现和验证辅助材料。保留平台原有其他必需目录。

运行 `python3 scripts/package.py` 生成项目旁的 `sap-order-sync.zip`。平台 Web IDE 没有 ZIP 导入入口时，按目录上传并替换同名文件；不要把 `sap-order-sync` 再套一层作为任务根目录。
