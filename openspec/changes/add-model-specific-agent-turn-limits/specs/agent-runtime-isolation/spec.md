## MODIFIED Requirements

### Requirement: Agent 容器必须具有明确的宿主机访问边界

系统 MUST 仅向 Agent 容器提供所需的目录挂载：OpenCode runtime 和任务临时目录为可写，图片、项目 Skill、轮次提醒插件源码及其运行时依赖为只读。轮次提醒插件和依赖 MUST 从与任务可写配置目录分离的受保护位置加载；任务中的 Bash、Python 或 Node 不能修改它们，也不能通过修改共享配置让后续任务加载不同的插件代码。容器 MUST 不获得项目根目录、用户目录、数据库凭据或 Docker socket 的访问能力。

#### Scenario: Agent 读取输入图片和受保护插件
- **WHEN** Agent 执行图片研究并加载轮次提醒插件
- **THEN** 它可以读取当前任务输入、Skill 和插件，但不能写入插件源码、插件依赖或未挂载的宿主机路径

#### Scenario: Agent 读取输入图片和 Skill
- **WHEN** Agent 执行图片研究
- **THEN** 它可以读取被挂载的图片和 Skill，但不能读取未挂载的宿主机路径

#### Scenario: 一个任务不能影响后续插件
- **WHEN** 某个任务通过 Bash、Python 或 Node 尝试修改轮次提醒插件、共享依赖或插件配置
- **THEN** 写入被文件权限或挂载边界拒绝，后续任务仍加载镜像中经过验证的同一插件版本

#### Scenario: Agent 尝试访问宿主 Docker
- **WHEN** Agent 在容器内检查 Docker socket
- **THEN** 容器中不存在可用的宿主 Docker socket
