## ADDED Requirements

### Requirement: 图片重试和修复接口必须明确表达处理方式
选中图片批量入口 MUST 保留现有请求字段的兼容解析，但在 `include_unready=true` 时明确按完整重试处理；scope 级 `/images/processing/unready` 入口 MUST 按“修复所有未就绪”处理；父 Job retry 入口 MUST 按完整重试处理；独立阶段入口 MUST 按只执行所选阶段处理。兼容字段只能转换为受约束的处理方式，不能继续作为运行时是否复用产物的布尔开关。

#### Scenario: 兼容请求触发选中图片完整重试
- **WHEN** 客户端向选中图片批量入口提交 `include_unready=true`
- **THEN** 服务端将其转换为完整重试处理方式
- **AND** 每张空闲图片的新父 Job 计划执行全部已启用阶段

#### Scenario: 父 Job retry 触发完整重试
- **WHEN** 客户端通过 `/images/processing/{job_id}/retry` 重试允许重试的终态 Job
- **THEN** 服务端创建新的完整重试 revision
- **AND** 对已有活动处理的图片返回 `409/image_processing_active`

### Requirement: 图片重试和修复响应必须使用稳定的逐图结果
选中图片完整重试和 scope 级修复接口 MUST 为每张目标图片返回 `meme_id`、`category`、可选 `reason` 和可选 `processing_job_id`。`category` MUST 是 `submitted`、`processing_active`、`not_needed` 或 `failed`：`submitted` 必须带本次新建的 Job 标识；`processing_active` 的 reason MUST 为 `image_processing_active` 且 MUST 不返回活动 Job 或 Task 标识；`not_needed` 的 reason MUST 为 `already_ready`；`failed` MUST 带有限稳定错误标识。`image_processing_plan_stale` 是 Job/阶段运行后的终态错误，不作为提交接口的即时分类，除非计划复核已经在提交事务内明确发现该错误。批量接口 MUST 以正常批量响应返回逐图结果，单张图片冲突不得把整批响应改为失败。

scope 级修复摘要 MUST 返回 `target_count`、`submitted_count`、`reused_count`、`conflict_count`、`not_needed_count`、`failed_count` 和 `results`。`reused_count` MUST 在兼容窗口中保留但固定为零；`conflict_count` MUST 等于 `processing_active` 结果数；其他计数 MUST 与逐图结果一致。响应不得把异步阶段尚未完成报告为处理成功。

#### Scenario: 返回批量部分成功结果
- **WHEN** 一次批量修复同时包含已提交、正在处理、无需修复和提交失败的图片
- **THEN** 响应逐图返回对应分类和原因
- **AND** `reused_count` 为零，其他汇总数量与逐图结果一致

#### Scenario: 枚举后确认图片无需修复
- **WHEN** scope 级修复在图片级锁内复核后确认一张候选图片已经没有需要修复的阶段
- **THEN** 该图片结果为 `not_needed`，reason 为 `already_ready`
- **AND** 系统不创建父 Job 或叶子 Task

### Requirement: 活动图片不得作为重试复用结果返回
完整重试、修复和独立阶段处理接口 MUST NOT 将已有活动父 Job 或 Task 返回为本次请求的复用成功。命中同图活动处理时，单图接口 MUST 返回 `409` 和 `image_processing_active`；批量接口 MUST 返回该图片的 `processing_active` 分类和 `image_processing_active` reason，并保持既有活动执行不变。API 文档和前端类型 MUST 同步标明这一兼容性变化。

#### Scenario: 单图请求命中相同活动 Job
- **WHEN** 用户对已有相同选项活动 Job 的图片再次提交完整重试
- **THEN** 系统返回 `409` 和 `image_processing_active`
- **AND** 不把原 Job 标记为本次请求已复用或已提交

#### Scenario: 批量请求命中活动图片
- **WHEN** 批量完整重试或批量修复中的一张图片已有活动 Job 或 Task
- **THEN** 该图片的逐图分类为 `processing_active`，reason 为 `image_processing_active`
- **AND** 其他图片的结果不受影响
