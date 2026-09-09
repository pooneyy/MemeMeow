## Why

当前 Agent 轮次展示通过查询 OpenCode SQLite 计算，而 Executor 运行期间没有使用已经输出的轮次事件。这样页面看到的轮次可能落后于实际执行，也无法在接近模型额度时提醒或可靠停止任务；模型重试或恢复时还可能重新获得完整轮次额度。

现在需要把轮次计数变成一次可信的运行时观测，让页面展示、收尾提醒和硬停止使用同一份事实，并允许不同模型拥有不同的轮次预算。

## What Changes

- 增加按模型配置的 Agent 轮次预算，区分正常轮次、提醒轮次和宽限轮次；配置在任务创建时冻结。
- Executor 增量解析 `opencode run --format json` 的 `step_start`/`step_finish` 事件，按 `part.id` 去重，形成统一的当前轮次状态。
- 由 OpenCode 插件尽量在达到提醒轮次时向模型加入“尽快完成并生成报告”的系统提示；受插件接口限制，提醒允许提前、延后或漏发，不作为强制停止依据。
- 在宽限轮次结束后，如果 Executor 观察到下一轮已经开始，就立即终止当前 OpenCode 进程组并返回稳定的轮次超限错误；这次模型请求可能已经发出，但不能继续调用工具绕过限制。
- 将 Executor 统一轮次状态提供给任务状态持久化和页面展示，避免页面和限制器各自读取 OpenCode SQLite 计算轮次。
- 恢复同一 OpenCode session 时沿用原任务和各 attempt 已消耗的轮次预算，不因重新启动进程而重置额度。
- OpenCode SQLite 继续用于 session 持久化、恢复和历史诊断；新任务的实时轮次不再以 SQLite 查询作为硬限制或主要展示来源。
- 为插件、Executor、任务状态接口和模型目录补充版本兼容、插件加载失败和事件格式异常的明确错误处理。

## Capabilities

### New Capabilities

- `agent-turn-limits`: 为 Agent 任务提供按模型的轮次预算、接近上限提醒、宽限和最终停止，并规定 Executor 统一轮次状态的来源和恢复语义。

### Modified Capabilities

- `task-status`: 任务状态中的 Agent 轮次字段改为优先使用 Executor 持久化的实时快照，同时保留历史任务无法获得快照时的只读兼容行为。
- `agent-runtime-isolation`: 规定轮次提醒插件必须从镜像内受保护的只读位置加载，不能从所有任务共用且可写的运行时配置目录加载。

## Impact

- Executor：任务状态、OpenCode stdout 增量读取、轮次状态机、宽限计数、进程组终止、session 恢复和错误分类。
- 公共模型目录与任务提交协议：增加每个模型的轮次策略，并在任务/attempt 中保存冻结后的策略和消耗快照。
- 后端持久化与任务 API：增加实时轮次快照及稳定的 `agent_turn_limit_exceeded` 等错误投影，页面继续使用现有 Agent 活跃度字段。
- 前端：继续展示已完成轮次和当前进行状态；在可用时显示接近上限或已因轮次停止的明确状态，不把轮次解释为完成百分比。
- Agent 镜像与部署：预装并校验版本匹配的 OpenCode 提醒插件，插件源代码和依赖只读，不能被任务中的 Bash、Python 或 Node 修改。
- 兼容与测试：覆盖逐行输出缓冲、重复事件、进程终止、插件失败、服务重启、session 恢复、跨模型配置和历史 SQLite 回退。
- 本 change 属于开源公共核心，应先在 MemeMeow 完成并形成可审核 commit；服务器版本仅在审核和上游同步后增加账户、订阅、计量和运行时路径适配。
