## MODIFIED Requirements

### Requirement: 语境研究任务必须提供可选的 Agent 活跃度摘要

任务状态接口 MUST 为 `meme_context_generation` 任务优先返回执行器持久化的实时轮次快照，包括 `agent_completed_turns`、`agent_turn_running` 和 `agent_last_activity_at`。`agent_completed_turns` MUST 表示当前任务主 session 已结束的 Agent 步骤数量，`agent_turn_running` MUST 表示是否存在已开始但尚未结束的步骤，`agent_last_activity_at` MUST 表示该任务最后一次已确认 Agent 活动的 UTC 时间；这些字段 MUST NOT 被解释为任务完成百分比或剩余轮次数。任务进入终态前 MUST 保存最后可确认的快照。对于没有新快照的历史任务，系统可以使用只读 OpenCode 会话元数据补齐字段，但不得用该兼容读取作为轮次限制依据。

#### Scenario: 新任务从执行器快照展示轮次
- **WHEN** 执行器已经确认 19 次步骤开始、18 次步骤结束且最近活动时间已持久化
- **THEN** 响应返回 `agent_completed_turns: 18`、`agent_turn_running: true` 和对应的 `agent_last_activity_at`

#### Scenario: Agent 已完成若干轮且新一轮正在执行
- **WHEN** 客户端查询一个具有 19 次步骤开始、18 次步骤结束及最近活动时间的语境研究任务
- **THEN** 响应返回 `agent_completed_turns: 18`、`agent_turn_running: true` 和对应的 `agent_last_activity_at`

#### Scenario: Agent 当前没有未完成轮次
- **WHEN** 客户端查询一个步骤开始数与步骤结束数相同的语境研究任务，且执行器快照已持久化
- **THEN** 响应返回相应的已完成轮次，并返回 `agent_turn_running: false`

#### Scenario: 任务终态保留最后快照
- **WHEN** Agent 任务因成功、普通失败或轮次超限进入终态
- **THEN** 任务详情仍返回终态前最后可确认的轮次数和最近活动时间，不因 OpenCode 进程退出或 SQLite 不可读而丢失已保存快照

#### Scenario: 历史任务没有实时快照
- **WHEN** 查询迁移前创建且没有执行器轮次快照的语境研究任务
- **THEN** 系统可以从可用的 OpenCode 会话元数据提供兼容活跃度字段；无法可靠读取时省略字段，不虚构零轮

#### Scenario: 非语境研究任务
- **WHEN** 客户端查询缓存生成或其他不由 OpenCode Agent 执行的任务
- **THEN** 响应不提供 Agent 活跃度摘要

### Requirement: Agent 活跃度观测必须只读且可降级

系统 MUST 以任务执行器产生并持久化的轮次快照作为新任务的主要活跃度来源。读取任务状态时 MUST NOT 为了新任务的实时轮次继续轮询或写入 OpenCode SQLite；OpenCode SQLite 仅可用于 session 持久化、恢复和没有实时快照的历史任务兼容诊断。无论执行器快照或兼容数据库是否不存在、繁忙、不可读、schema 不兼容或找不到任务 session，系统 MUST 继续返回正常任务状态，并省略无法确认的活跃度字段。活跃度观测失败 MUST NOT 改变任务执行、轮次限制或任务终态。

#### Scenario: 新任务使用持久快照
- **WHEN** 客户端查询正在运行的新任务，且 PostgreSQL 中存在最近的执行器轮次快照
- **THEN** 任务状态直接返回该快照，不访问 OpenCode SQLite 计算当前轮次

#### Scenario: 历史兼容数据库不可用
- **WHEN** 客户端查询没有执行器快照的历史任务，且其 OpenCode 会话数据库不存在、被锁定或 schema 不兼容
- **THEN** 任务状态仍成功返回现有任务字段，并省略 Agent 活跃度字段

#### Scenario: OpenCode 会话数据库不可用
- **WHEN** 客户端查询没有执行器快照的历史任务，且 OpenCode 会话数据库不存在、被锁定或无法按预期 schema 查询
- **THEN** 任务状态仍成功返回现有任务字段，并省略 Agent 活跃度字段

#### Scenario: 历史任务没有对应 session
- **WHEN** 客户端查询一个没有执行器快照且没有对应 OpenCode session 的语境研究任务
- **THEN** 任务状态正常返回任务信息，且不虚构零轮或最近活动时间

#### Scenario: 查询任务列表
- **WHEN** 客户端查询包含多个语境研究任务的任务列表
- **THEN** 系统使用持久快照批量装配活动字段，并仅对缺少快照的历史任务执行有界兼容读取

#### Scenario: 活跃度观测失败不改变硬限制
- **WHEN** 页面查询活跃度时发生读取错误
- **THEN** 该错误不改变已经冻结的轮次策略，也不阻止或延后执行器对轮次上限的处理
