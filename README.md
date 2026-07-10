<p align="center">
  <img src="assets/evolva-poster.jpeg" alt="Evolva - Local Agent Harness" width="680" />
</p>

<h1 align="center">Evolva</h1>

<p align="center">
  <strong>把 Agent 跑在本地，把执行留在证据里，把风险关进边界内。</strong><br />
  一个面向真实仓库的 Agent 工作台：能规划、调用工具、恢复任务，也能解释自己做过什么。
</p>

<p align="center">
  <a href="README.en.md">English</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#核心能力">核心能力</a> ·
  <a href="#loop-engineering">Loop Engineering</a> ·
  <a href="#生产运行边界">生产运行</a>
</p>

<p align="center">
  <a href="https://github.com/koppx/Evolva/stargazers">
    <img alt="GitHub stars" src="https://img.shields.io/github/stars/koppx/Evolva?style=for-the-badge&logo=github&label=Stars&color=EAD58B&labelColor=0B0B0F&cacheSeconds=1800" />
  </a>
  <img alt="Local First" src="https://img.shields.io/badge/Local--First-Agent%20Harness-2E8B57?style=for-the-badge&labelColor=0B0B0F" />
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-FFF0B3?style=for-the-badge&labelColor=0B0B0F" />
  <img alt="License" src="https://img.shields.io/badge/License-Apache--2.0-D6A84F?style=for-the-badge&labelColor=0B0B0F" />
</p>

<p align="center">
  <img src="assets/tui-mockup.svg" alt="Evolva TUI Workbench" width="100%" />
</p>

把 Agent 放进真实仓库，问题就不只是“答得对不对”：它还要找得到上下文、管得住工具、失败后接得上，结果也得经得起复查。Evolva 就是围绕这几件事做的。

```text
Plan -> Act -> Observe -> Evaluate -> Evolve
```

- **执行前有边界**：Policy、审批和 Sandbox 共同约束文件、Shell、Python 与 MCP。
- **执行中有状态**：Workflow、Loop 和 Session 保存进度，失败后可以恢复或重试。
- **执行后有证据**：Trace、Artifact 与 Eval 记录结果，经验经过验证后才进入 Memory / Skill。

## 快速开始

需要 Python 3.10+。推荐使用 [uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/koppx/Evolva.git
cd Evolva
uv sync
uv run evolva
```

也可以安装为全局命令：

```bash
pipx install git+https://github.com/koppx/Evolva.git
evolva
```

进入 TUI 后，用 `/config wizard` 配置任意 OpenAI-compatible 模型；没有模型时，文件工具、Trace、Workflow 和 Eval 仍可在本地规则模式下使用。

```text
/config wizard             配置 model、base_url、api_key 和 temperature
/repo build                建立当前仓库索引
分析这个项目的风险         直接开始任务
/trace list                查看任务执行记录
/session list              查看持久化会话
/cancel                    停止当前任务，也可按 Ctrl+K
```

运行态统一保存在 `.evolva/`，不会混入源码目录。可通过 `EVOLVA_RUNTIME_HOME` 把它放到独立磁盘或受控目录。

> **安全提示**：默认 `local` backend 是开发模式，不隔离宿主机读取或进程。生产环境必须使用隔离 backend；见[生产运行边界](#生产运行边界)。

## 核心能力

| 场景 | Evolva 提供什么 | 入口 |
| --- | --- | --- |
| 理解仓库 | 文件、符号、引用和可插拔语义检索 | `/repo` |
| 执行任务 | 受 Policy 与审批约束的文件、Shell、Python、Web 和 MCP 工具 | 对话 / `/run` |
| 处理复杂流程 | 可恢复 Workflow 与带质量门的 Loop | `/workflow` / `/loop` |
| 组织协作 | Task Router、受限子 Agent、并行角色和结果综合 | `/agents` |
| 复盘与回归 | Trace、Artifact manifest、Replay 和 JSONL Eval | `/trace` / `evolva eval` |
| 沉淀经验 | 有命名空间、TTL、冲突检测和验证门的 Memory / Skill | `/memory` / `/dream` |

## 架构总览

<p align="center">
  <img src="assets/architecture.png" alt="Evolva Agent Architecture" width="100%" />
</p>

架构分成三条主线：

1. **Reasoning & State**：TUI、Core、Session、Context 和 Repo Index 组织任务上下文。
2. **Guarded Execution**：工具调用经过 Policy、审批与 Sandbox；生产命令执行要求强隔离。
3. **Evidence & Learning**：Trace 和 Eval 负责证明结果，Dream 只提升通过 verifier 的经验。

## Loop Engineering

Loop 面向“需要反复做、必须验收”的工程任务。自然语言需求先生成草案，用户确认后才执行；每个阶段都可以声明依赖、预算、质量门和产物。

<p align="center">
  <img src="assets/loop-engineering.jpeg" alt="Evolva Loop Engineering" width="100%" />
</p>

```text
/loop 做一次发布检查：运行测试、类型检查和安全 Eval
/loop revise 失败时只修复相关模块，最多重试两轮
/loop confirm
/loop execute
/loop save release-check
```

内置 Loop：

| Loop | 用途 |
| --- | --- |
| `release-readiness-loop` | 发布前检查测试、CLI、Trace 和 Dream 状态 |
| `eval-regression-loop` | 运行回归并把失败保留为可验证候选 |
| `repo-improvement-loop` | 索引仓库、扫描问题并回流运行证据 |
| `dream-loop` | 从 Trace / Eval / Memory 生成改进候选 |

Loop 更接近工程闭环；Workflow 是它下面的通用 DAG 执行层。

## Workflow / MCP / Memory

<p align="center">
  <img src="assets/workflow-mcp-memory.png" alt="Evolva Workflow MCP Memory Loop" width="100%" />
</p>

Workflow 支持成功节点恢复、节点级重试、条件、超时、有限并行和失败补偿。下面是一个最小示例：

```json
{
  "id": "repository_review",
  "nodes": [
    {
      "id": "search",
      "type": "tool",
      "tool": "repo_index_search",
      "args": {"query": "policy sandbox"},
      "retries": 1
    },
    {
      "id": "review",
      "type": "role",
      "role": "reviewer",
      "depends_on": ["search"],
      "task": "基于 {{search}} 给出风险结论"
    }
  ]
}
```

```bash
evolva workflow path/to/workflow.json --yes
```

MCP server 默认只继承白名单环境变量，并支持 trust level、工具 allow/deny list 和 Docker 隔离。Memory / Skill 则把“可审计地保存”与“允许进入 prompt”分开，过期、冲突、隔离或已回滚内容不会自动影响 Agent。

## 自我进化

<p align="center">
  <img src="assets/evolva-dreaming-loop.jpeg" alt="Evolva Dreaming Loop" width="100%" />
</p>

这里的“进化”不是让 Agent 不受控地改自己，而是一个保守的晋升流程：

```text
Evidence -> Hypothesis -> Candidate -> Verifier -> Promotion
```

`/dream apply` 只生成候选；`/dream verify --promote` 才会把通过验证的 lesson 写入 Memory / Skill。晋升后的 verifier 如果回归，关联资产会按 fingerprint 回滚；也可以显式执行 `evolva dream rollback <candidate_id>`。

```text
/evolve audit
/dream
/dream backlog
/dream verify --promote
```

## Eval 与可观测性

Eval Harness 把 Agent 行为写成 JSONL 契约，同时检查回答、Trace 事件、工具顺序、Artifact、Policy 决策和运行指标。

```bash
evolva eval evals/tasks/security.jsonl --yes \
  --baseline evals/baselines/security.json \
  --min-score 1.0 \
  --no-regression
```

真实模型评测可以额外限制可用性、P95 和成本：

```bash
evolva eval path/to/live-suite.jsonl \
  --require-llm \
  --max-p95-ms 30000 \
  --max-cost-usd 1.00
```

运行指标保存在本地，并可导出为 Prometheus 文本或 OTLP-shaped JSON：

```bash
evolva metrics prometheus
evolva metrics otlp --limit 1000
evolva metrics prune
```

## 生产运行边界

Evolva 能运行代码，因此 `workspace` 目录不等于安全隔离。边界需要分层理解：

| 层级 | 能保证什么 |
| --- | --- |
| 文件工具路径检查 | Evolva 内置文件工具不能越过项目 root |
| Policy 与审批 | 危险模式可拒绝，高风险调用可要求逐次或本会话批准 |
| Local backend | 便于开发，但宿主读取、进程和网络并未隔离 |
| Docker backend | 只读挂载项目、显式可写目录、capability drop、no-new-privileges 和资源限制 |

生产启动建议：

```bash
export EVOLVA_PROFILE=prod
export EVOLVA_SANDBOX_BACKEND=docker
export EVOLVA_SANDBOX_CONTAINER_NETWORK=none
export EVOLVA_RUNTIME_HOME=/secure/runtime/evolva

evolva sandbox smoke
evolva
```

生产 profile 在没有隔离 backend 时会拒绝 Shell / Python。第三方 MCP 也应单独设置 `isolation: docker`、环境白名单和工具 allowlist。

API key 默认可保存在权限为 `0600` 的本地 runtime config。需要系统凭据库时：

```bash
uv sync --extra credentials
export EVOLVA_CREDENTIAL_BACKEND=keyring
uv run evolva
```

升级已有运行态前先 dry-run：

```bash
evolva migrate state
evolva migrate state --apply
```

更多说明见 [Production Operations](docs/production-operations.md)、[State Migrations](docs/state-migrations.md) 和 [Security Policy](SECURITY.md)。

## 常用命令

<details>
<summary><strong>TUI Slash Commands</strong></summary>

```text
/config wizard                  配置模型
/session list|new|use|rename    管理会话
/session fork|retry             分支或重跑最近一轮
/cancel                         停止当前任务
/repo build|status|search       管理仓库索引
/trace list|show|context        查看执行证据
/memory [query|stats|recent]    查看长期记忆
/mcp add|remove|tools|health    管理 MCP server
/loop <request>                 生成 Loop 草案
/loop confirm|execute|save      确认、执行和保存 Loop
/dream status|backlog|verify    管理改进候选
/workflow <json-path>           执行 Workflow
/run <tool> <json>              直接调用工具
/help                           查看完整帮助
```

</details>

| 快捷键 | 作用 |
| --- | --- |
| `F2` / `F4` | 切换模型 / 打开配置向导 |
| `Ctrl+R` / `Ctrl+X` | 查看 Trace / 最新上下文事件 |
| `Ctrl+T` | 显示或隐藏工具日志 |
| `Ctrl+K` | 请求停止当前任务 |
| `Tab` | 补全 Slash Command |
| `Esc` | 清空当前输入 |

## 开发与验证

```bash
uv sync --extra dev
uv run ruff check evolva tests
uv run mypy evolva
uv run coverage run -m pytest -q
uv run coverage report
uv build
```

CI 还会运行 `smoke`、`repo_index`、`security`、`scorers` 和 `trace_artifacts` 五组 Eval baseline。

## 项目治理

- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [Apache-2.0 License](LICENSE)

## Star History

<p align="center">
  <a href="https://www.star-history.com/#koppx/Evolva&Date">
    <img src="https://api.star-history.com/svg?repos=koppx/Evolva&type=Date" alt="Evolva Star History" width="100%" />
  </a>
</p>

<p align="center">
  <strong>Evolva</strong> · Local-first, inspectable, self-evolving Agent Harness.<br />
  如果你也在构建可评测、可回放、可治理的 Agent 系统，欢迎 Star。
</p>
