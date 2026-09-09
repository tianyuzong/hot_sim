# 后端 Agent 配置与排障

## 当前状态（2026-09-07）

项目：`/data/yihongzhu/_zty/hot_sim`，服务账户：`yihongzhu`。
用户已确认当前服务器的 Codex 不可用，要求先整理其余后端；真实模型联调已停止。
**Codex 在线建模、连续对话与优化闭环尚未验收通过。**

停止前的检查发现：CLI 为 `0.153.4`；最小结构化请求曾成功，完整 STL 草案请求曾在
120 秒后返回 503。另一次较长观察中出现过模型响应及后续流连接重试，但未完成草案工作流。
这些记录不能证明服务可稳定使用，也不能把故障统一归因于目录权限。
当前 SSH 会话能创建 loopback socket，Codex 目录权限为 `700`；以前文档中的只读挂载、
`EPERM` 和 `777` 是历史环境记录，不是当前环境检查结果。

项目 `.env` 仍选择 `codex`，本次未切换规划器、修改凭据或共享 Codex 配置。
现有 `127.0.0.1:18080` 预览进程使用 `.e2e-data`，不是默认 `data` 目录。
2026-09-07 热仿真修正通过最终 152 项回归后，已按原数据目录、端口、CPU 后端和 Codex 设置重启
（PID `3105944`）；健康检查与工作台 HTTP 200，原有研究仍保留。这只证明应用启动，
没有恢复真实模型联调，也不意味着 Codex 可用。服务没有热重载，后续更新仍须显式重启。

## 三种明确选择的模式

| `THERMOFLOW_PLANNER` | 用途 | 必需条件 |
| --- | --- | --- |
| `codex` | 现有 CLI 生成结构化草案和受授权优化决策 | 服务用户自己的 CLI、配置、登录和提供方连接 |
| `openai` | 后端通过 Responses API 生成相同结构化数据 | 服务端 `OPENAI_API_KEY` 与可用的 `OPENAI_MODEL` |
| `deterministic` | 离线规则演示、数值和工作流回归 | Python 项目依赖；不具备完整自然语言理解能力 |

模式之间不会因错误而自动切换。模型失败不确认输入、不触发求解，也不覆盖已保存草案。
`GET /health` 只报告配置和计算环境，不发送模型请求；`status=ok` 不能作为模型可用性证据。

## 不依赖模型服务的验证

从项目根目录执行（本机已安装依赖的环境为 `.venv312`）：

```bash
.venv312/bin/python -m pytest -q
node --check src/thermoflow/web/assets/app.js
node tests/browser/modeling_state.mjs
node tests/browser/viewport_geometry.mjs
```

Python 回归使用模拟模型响应与真实本地数值计算，不发送真实 Codex/OpenAI 请求。
覆盖草案、来源校验、应用/撤销、独立用户确认、网格、求解及受授权优化。
Node 检查覆盖表单状态与几何运算，不能替代真实浏览器渲染验收。

需要独立离线预览时，可显式指定临时数据目录与空闲端口：

```bash
cd /data/yihongzhu/_zty/hot_sim
preview_data=$(mktemp -d /tmp/thermoflow-offline-preview.XXXXXX)
THERMOFLOW_PLANNER=deterministic \
THERMOFLOW_COMPUTE=cpu \
THERMOFLOW_DATA_DIR="$preview_data" \
THERMOFLOW_HOST=127.0.0.1 \
THERMOFLOW_PORT=18081 \
.venv312/bin/python -m thermoflow
```

按 Ctrl+C 停止。临时目录包含此次预览数据，停止服务不会删除它。
这条命令不改变 `.env` 或其他服务的数据目录。

## 错误的含义

| 提示或响应 | 处理方式 |
| --- | --- |
| Codex 正在处理其他请求 | 当前每 API 进程共用一个调用槽，等待后重试 |
| 模型响应超时 | 草案仍保留；先查提供方状态和耗时，不重复并发提交 |
| 认证失败 | 部署管理员检查该实例的服务端凭据 |
| 限流或额度不足 | 稍后重试或检查额度；不要绕过限流 |
| 无法连接模型服务 | 检查目标提供方的 DNS、代理和连接状态 |
| 未返回结构化结果／结构化校验失败 | 检查模型和 Schema 兼容性；不应用无效内容 |
| 材料或物理输入校验失败（409） | 在表单中核对来源、单位和条件；不当作登录故障 |

草案创建和连续对话将已脱敏的模型不可用错误返回为 503，保留具体原因。
优化接口仍返回持久化运行记录，调用方必须检查 `status` 与 `failure`，不能只检查 HTTP 200。
非模型工具的未知异常只保存固定提示，服务日志记录运行标识与异常类型，不记录异常原文。

OpenAI 草案和优化使用相同的 45 秒 SDK 请求超时与最多 2 次重试。
此值不是整个接口的总时限：SDK 重试、最多两次草案策略校验以及多轮优化可延长总耗时。
Codex 的单次 CLI 总时限由 `THERMOFLOW_CODEX_TIMEOUT_SECONDS` 控制（默认 120，范围 5–600 秒），
超时会终止并回收该 CLI 进程组；后台计算任务使用独立超时设置。

## Codex 恢复后的验收顺序

1. 在服务用户环境中核实 CLI 版本、`codex doctor`、登录状态及实际提供方连接。
   使用配置中现有提供方，不默认假设连接的是 `api.openai.com`。
2. 确认 `THERMOFLOW_CODEX_EXECUTABLE` 与 `THERMOFLOW_CODEX_HOME` 指向该账户的安装。
   当前路径分别为 `/home/yihongzhu/.npm-global/bin/codex` 和 `/data/yihongzhu/_zty/.codex`。
3. 使用隔离测试数据完成完整 STL 草案，检查材料来源、单位、支持能力和 `needs_input` 状态。
4. 验证连续对话读取当前表单，应用/放弃/撤销正常，迟到响应不能覆盖新输入。
5. 独立确认输入后生成并检查网格，再由确定性求解器计算；验证受授权优化生成独立研究。
6. 最后在真实浏览器验证以上操作，再记录在线验收通过。

无需为联调开放通用 sudo、关闭沙箱或把目录改成全员可写。
不要读取、复制或提交 `auth.json`。仅当实际报错为权限问题时，才检查服务账户权限和外层运行策略。
不要修改共享模型配置来掩盖应用错误。

CLI 的 [非交互结构化输出](https://learn.chatgpt.com/docs/non-interactive-mode)
和 [配置参考](https://learn.chatgpt.com/docs/config-file/config-reference) 可用于核对版本兼容性；
应用仍必须独立校验返回内容。外部模型的数据保留策略由提供方决定。
