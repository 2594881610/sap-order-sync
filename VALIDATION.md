# 验证记录

验证日期：2026-10-27（Asia/Shanghai）。这是本地 Docker / HTTP / MySQL 的实际运行结果，不是模型难度评测。

## 环境与执行

- Docker Desktop / Engine 29.3.1，Linux ARM64容器。
- Temurin Java 17.0.15、Maven 3.9.9、Spring Boot 2.5.15、MySQL 8.4.13、Connector/J 8.4.0。
- 最终执行：使用临时匿名Docker CLI配置连接本机Docker Desktop，运行 `python3 scripts/validate.py`（公开镜像，无私有仓库凭据）。六个场景分别创建全新数据库和容器；每次评分重新清除编译产物并离线编译当前源码。
- 最终环境构建已通过；镜像不含IDE配置、参考解或评分文件。首次拉取镜像的网络问题通过临时下载配置解决，未修改全局Docker或代理配置。
- 三轮正反向场景总耗时：116.5秒（不含首次下载和构建）。

## 本次平台启动兼容修复

- 平台错误指向 `environment/docker-compose.yaml` 的 `db.volumes`，发生在参考解执行之前。
- 新增 `environment/Dockerfile.db`，通过COPY将两份初始化SQL打入MySQL镜像。
- db服务改为构建 `sap-order-sync-db:local`，移除Compose的volumes配置；本地验证同时构建main和db。
- 平台随后拒绝Compose中的美元符号。两份构建上下文已改为字面量相对路径点号，不再使用变量插值；从其他工作目录解析也能定位到environment目录。
- 本地验证入口新增美元符号检查；已验证违规配置在调用Docker之前被拒绝。
- 对照报错中的服务键白名单完成本地静态核对；此核对不替代平台实际检测。
- 三轮日志均确认执行镜像内的01-schema.sql和02-seed.sql；镜像内SQL摘要与源码一致。
- Java代码、接口、初始化SQL、参考解、评分脚本及用户现有task.toml和instruction.md均未修改。
- Web IDE需更新 `environment/docker-compose.yaml` 并新增 `environment/Dockerfile.db`，同时保留完整initdb目录，再进行平台检测。

## 三轮结果

| 独立场景 | reward | 实际结果 |
|---|---|---|
| baseline-1 | 0 | 实际产生重复订单；最大17条 |
| oracle-1 | 1 | 18项全部通过；每组始终1条 |
| baseline-2 | 0 | 实际产生重复订单；最大12条 |
| oracle-2 | 1 | 18项全部通过；每组始终1条 |
| baseline-3 | 0 | 实际产生重复订单；最大28条 |
| oracle-3 | 1 | 18项全部通过；每组始终1条 |

每轮包含两个独立JVM、10组每组32个相同订单请求，以及32个不同订单的并发请求。负向验证额外要求真实观察到重复记录，不会把构建失败、服务启动失败或仅缺少交付脚本当作并发故障复现。

故障版并非每一组都必定重复，个别组可能恰好只产生1条记录；本次三个全新数据库环境均在多组请求中复现重复。业务代码未添加人为延迟。

参考修复三轮均满足：重复请求全部成功、ID一致、真实数据库每单一行、不同订单完整落库、字段更新正确、非法输入不落库、精确编码比较、合法字段边界、重启后ID稳定和历史记录保留。

## 迁移与异常处理

- 每轮参考解完整执行两次，评分内又重复执行迁移两次，均成功且不改变已有数据。
- 在额外的独立探针库中插入两条重复订单，参考迁移明确失败，原始两行及数量和物料均被保留。
- 每轮将旧评分预置为1，再主动制造编译错误，评分入口非零退出且结果被重置为0；对应证据见 `validation/reports/*-build-failure-reward.txt`。
- 所有验证创建的容器、网络和数据库卷已由脚本清理。构建镜像和依赖缓存保留，便于复现。

## 静态检查与交付边界

Python AST、Shell语法、Maven XML、Compose配置、TOML schema 1.3及文件空白检查已通过。当前目录不是Git仓库，使用逐文件 `git diff --no-index --check` 检查空白；没有修改hdjz工程。

详细评分见 `validation/reports/`，总体结果见 `validation/summary.json`；本机完整执行、编译和服务日志保存在未打包的 `artifacts/`。

平台此前两次尝试启动Oracle评测，先因Compose的db.volumes不在白名单、后因美元符号变量插值被禁止，均在启动阶段失败，参考解尚未执行。本次已修复该配置并完成本地复测；本次字面量路径版本尚未在平台重新检测，未运行Harbor CLI或候选模型评测，不能据此认定平台Oracle通过或确定L1/L2/L3难度。本项目为AI辅助精简重写及演示数据，材料来源限制见 `PROVENANCE.md`。
