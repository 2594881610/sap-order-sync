# v2 验证记录

验证日期：2026-10-27（Asia/Shanghai）。这是本地 Docker、HTTP 和 MySQL 的实际运行记录，不是新版模型难度评测。

## 执行环境

Docker Desktop / Engine 29.3.1，Linux ARM64；Temurin Java 17.0.15、Maven 3.9.9、Spring Boot 2.5.15、MySQL 8.4.13、Connector/J 8.4.0。

先执行 `python3 scripts/validate.py --rounds 1`，完整构建 main 和 db 并完成首轮。补齐数据库锁测试后，最终执行 `python3 scripts/validate.py --rounds 3 --reuse-images`。验证器复制当前 Java 源码、脚本、测试，清除 target 后重新离线编译；六个场景使用相互独立的全新数据库。最终三轮耗时 184.6 秒，不含镜像构建。

为访问公开镜像，使用自动删除的临时匿名 Docker CLI 配置连接本机 Docker Desktop，未修改全局 Docker、凭据或代理配置。

## 三轮结果

| 场景 | reward | 实测结果 |
|---|---|---|
| baseline-1 | 0 | 复现重复订单，最大 20 条；并发状态错误也被检出 |
| baseline-2 | 0 | 复现重复订单，最大 13 条；并发状态错误也被检出 |
| baseline-3 | 0 | 复现重复订单，最大 22 条；并发状态错误也被检出 |
| oracle-1 | 1 | 32 项全部通过 |
| oracle-2 | 1 | 32 项全部通过 |
| oracle-3 | 1 | 32 项全部通过 |

每场评分包含两个独立 JVM。每次测试包括 10 组各 32 个同订单请求、6 组各 32 个自动/手工排产竞争、4 组各 16 个反向重叠批次、4 组各 16 个物料变更/排产竞争，以及 32 个不同订单并发。

## 业务与事务验证

- 串行创建、更新、重复排产；订单/计划/工序主键稳定。
- 已排产计划生产物料不被上游覆盖，计划数量更新，工序数量快照保留。
- 精确字符串匹配、合法数量边界、单单与批量非法输入不落库。
- 缺少路线、不存在或已完成计划导致整批回滚。
- 临时 CHECK 约束使第二道工序实际写入失败，三表完整回滚；移除约束后重试成功。
- 独立事务持有订单及计划锁，提交已完成状态；参考解等待后读取最新状态，返回 409 且不产生工序。故障版该检查三轮均失败。
- 一个订单处于数据库锁等待时，同一 JVM 仍能处理另一个订单。
- 服务重启重试、三表历史记录保护、迁移重复执行。

## 迁移与评分异常

每个参考场景完整执行 solve.sh 两次，评分内执行迁移两次。另在三个隔离探针库分别注入重复订单号、重复计划关联、重复工序序号，迁移均非零退出且保留原始重复记录。三个参考场景均执行该探针检查。

每个参考场景将旧分数设为 1 并主动制造编译错误，评分入口仍先将 reward.txt 重置为 0；证据在 `validation/reports/*-build-failure-reward.txt`。

临时 CHECK 约束已移除，锁会话已关闭。验证创建的六套容器及网络、数据库卷已清理。镜像与依赖缓存保留供复现。

## 静态检查与差异

Python AST、Shell 语法、Maven XML、容器 Python 3.12 的 TOML 解析、Compose 配置与服务键白名单、逐文件 `git diff --no-index --check` 均通过。本机系统 Python 不含 tomllib，因此 TOML 校验使用镜像内 Python，未安装额外依赖。空白检查发现并清除了故障源码两处空行尾随空格，不影响逻辑。

对照升级前备份确认 Dockerfile、Dockerfile.db、docker-compose.yaml 和 pom.xml 原样保留；Compose 无 volumes、无美元符号。task.toml 只替换 metadata，组织/题目名称和超时等配置保持原样。原 hdjz 工程和用户 IDE 配置均未修改。

详细报告见 `validation/reports/`，源码摘要见 `validation/source-sha256.json`。完整编译、HTTP 服务和数据库执行日志位于未打包的本机 `artifacts/`。

## 未执行及交付边界

新版尚未提交平台 Oracle、Harbor CLI 或候选模型评测，没有推送远程 Git。旧版候选模型满分的结果不能用于判断新版难度；metadata 的 hard 是出题预期，不代表已达到平台门槛。

本项目是基于真实业务调用链的 AI 辅助精简改写，使用演示数据；详见 PROVENANCE.md。没有连接真实 SAP 或生产数据库。
