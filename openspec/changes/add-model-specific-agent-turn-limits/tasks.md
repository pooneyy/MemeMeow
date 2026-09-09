## 1. 公共轮次协议和模型策略

- [ ] 1.1 在公共模型目录中增加正常轮次、提醒轮次和宽限轮次策略，并实现正整数、边界关系和硬停止轮次校验
- [ ] 1.2 增加不依赖数据库和网络的 OpenCode JSON 增量解析器，支持未完成行缓冲、行大小限制、session 绑定和 `(sessionID, part.id, part.type)` 去重
- [ ] 1.3 实现统一轮次快照的序列化和反序列化，覆盖 `completed_turns`、`consumed_turns`、`turn_running`、`last_activity_at` 以及提醒状态
- [ ] 1.4 为轮次策略缺失、事件格式错误、session 绑定冲突、插件不可用和 `agent_turn_limit_exceeded` 建立稳定错误集合及定向单元测试

## 2. Executor 实时计数和硬停止

- [ ] 2.1 为 Executor attempt 状态增加冻结策略、累计轮次和公开轮次快照，并让 GET 任务状态返回有限字段而不暴露原始事件内容
- [ ] 2.2 在现有临时 stdout 文件上接入增量读取，在进程轮询期间解析新增 JSON 行，保留完整文件用于终态 session 绑定和错误诊断
- [ ] 2.3 在收到唯一 `step-start` 时更新快照并判断硬停止边界，在收到 `step-finish` 时更新完成轮次；处理重复、乱序、截断和冲突事件
- [ ] 2.4 收到超过“正常上限加宽限轮数”的下一个 `step-start` 后立即复用进程组终止和 wait 回收流程，接受该次模型请求可能已经发出，并区分 `agent_turn_limit_exceeded`、`unknown_execution`、用户取消和普通超时
- [ ] 2.5 为恢复 attempt 传入并校验原策略和累计消耗，禁止进程或 Executor 重启后把轮次重置为零
- [ ] 2.6 增加 Executor 进程夹具测试，覆盖逐行缓冲、同一 part 重放、多个工具调用、硬停止、终态快照和旧 attempt 迟到事件

## 3. 后端持久化、状态接口和页面复用

- [ ] 3.1 增加 PostgreSQL migration，为 Task 和 Agent attempt 保存冻结策略、累计消耗、当前/最终轮次快照及必要的约束和索引
- [ ] 3.2 扩展 `TaskRecord`、任务 repository 和 claim fencing 更新，使活动快照按当前 claim 写入，终态提交前保存最后快照
- [ ] 3.3 扩展 `ExecutorTaskResponse` 和 `AgentExecutorClient`，在已有终态轮询中解析快照并向 `OpenCodeRunner` 提供有界活动回调
- [ ] 3.4 让任务服务把活动回调写入 PostgreSQL，同时保留最后可信快照；快照写入失败不得改变 Executor 的硬停止结果
- [ ] 3.5 更新任务摘要和公开 DTO，使新任务优先读取 PostgreSQL 快照，旧任务没有快照时才使用只读 OpenCode SQLite 兼容回退
- [ ] 3.6 更新前端类型、任务列表和详情状态，继续展示已完成轮次/进行中/最近活动，并明确展示轮次超限失败而不显示完成百分比
- [ ] 3.7 增加 repository、API、恢复和前端测试，覆盖新旧任务来源、快照缺失、SQLite 不可用、服务重启和 claim fencing

## 4. OpenCode 提醒插件和运行隔离

- [ ] 4.1 编写与生产 OpenCode 版本匹配的本地 JavaScript 提醒插件，使用 awaited 系统提示钩子追加一次性中文收尾提醒
- [ ] 4.2 让任务配置显式引用镜像内只读插件，并传入模型策略、目标模型和版本信息；插件无法加载或初始化时在模型调用前返回稳定错误
- [ ] 4.3 让插件按主 session 和可确认历史尽力判断提醒轮次，尽量过滤标题、压缩和子任务等非主轮次；明确允许提醒提前、延后或漏发，插件不参与硬停止或任务数据库写入
- [ ] 4.4 将插件源码和依赖预装到 Agent 镜像的受保护路径，补充运行时权限检查，确认任务中的 Bash、Python、Node 不能修改或替换它们
- [ ] 4.5 增加插件加载失败、版本不兼容、提醒最多一次、无法确认轮次、运行中提醒失败仍继续、Executor 硬限制不受插件影响和隔离边界的定向测试，覆盖生产 OpenCode `1.18.18`；测试不要求精确命中第 9 轮

## 5. 集成验证和发布准备

- [ ] 5.1 运行模型策略、事件解析、Executor 状态接口和错误映射的完整单元测试
- [ ] 5.2 运行 PostgreSQL migration、任务状态轮询、并发 claim、服务重启、session 恢复和旧 attempt fencing 集成测试
- [ ] 5.3 在 Docker Agent 环境执行真实任务，验证页面轮次与 Executor 快照一致、免费模型在第 9 轮附近尽力提醒，以及收到第 16 个 `step-start` 后立即停止并保留诊断
- [ ] 5.4 验证旧任务 SQLite 活跃度回退和新任务 SQLite 不可用时的降级行为，确认 OpenCode session 恢复仍然可用
- [ ] 5.5 完成安全、并发、迁移、回滚和前端窄屏回归，整理开源仓库的边界清晰 commit、测试结果和待用户审核的 SHA
