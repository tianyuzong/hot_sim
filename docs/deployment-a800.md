# Ubuntu / A800 部署

ThermoFlow 支持 Ubuntu 22.04/24.04、Python 3.10 及以上版本。STL 的体素、
装配和后处理仍在 CPU 上完成；占主要数值求解成本的稀疏线性系统可由 CuPy
在 NVIDIA GPU 上执行。结果中的 `compute_backend` 和 `compute_device` 记录实际
使用的后端，`GET /health` 返回启动时的 CUDA 可用性。

## 当前 A800 主机

目标主机使用 Ubuntu 22.04、NVIDIA 580 驱动和两张 A800 80GB。由于主机没有
Docker/Compose、没有免密 sudo，实际部署使用 Python 虚拟环境与用户级 systemd，
文件位于 `~/data44T/zongtianyudata/thermoflow`，避免占用接近满载的系统盘。
安装脚本在系统缺少 `python3.10-venv` 时，会把 `virtualenv` 引导包也放在发布目录，
不需要 sudo。

```bash
cd ~/data44T/zongtianyudata/thermoflow/current
cp deploy/ubuntu/.env.a800.example .env
# 检查 CUDA_VISIBLE_DEVICES、数据目录和规划器配置
bash deploy/ubuntu/install-user-service.sh "$PWD"
curl http://127.0.0.1:8000/health
```

生产配置使用 `THERMOFLOW_COMPUTE=cuda`。如果 CuPy、驱动或 GPU 不可用，健康
检查的 `compute.ready` 为 `false`，求解直接失败。开发机可以使用 `auto`，此时
CUDA 错误会回退 SciPy CPU，并在结果的 `compute_fallback_reason` 中留下原因。

服务仅绑定远端回环地址。客户端通过 SSH 隧道访问：

```bash
ssh -N -L 18000:127.0.0.1:8000 A800
```

随后打开 `http://127.0.0.1:18000/docs`。服务管理命令：

```bash
systemctl --user status thermoflow.service
journalctl --user -u thermoflow.service -f
systemctl --user restart thermoflow.service
```

## Docker 备选方案

已安装 Docker Engine、Compose 和 NVIDIA Container Toolkit 的节点可以使用：

```bash
cp deploy/ubuntu/.env.a800.example .env
docker compose -f compose.a800.yaml up -d --build
docker compose -f compose.a800.yaml exec thermoflow nvidia-smi
```

镜像基于 CUDA 12.6 / Ubuntu 22.04，并安装 `cupy-cuda12x`。CUDA 12.x 要求至少
525 系列 Linux 驱动；A800 上的 580 驱动满足要求。Compose 只保留一个 GPU，
具体物理卡由宿主机的 `CUDA_VISIBLE_DEVICES` 或运行环境调度策略决定。

## 使用 Codex Harness

后端环境设置 `THERMOFLOW_PLANNER=codex`，建模对话与优化决策都会调用已安装的 Codex CLI，
不调用 OpenAI Python SDK，也不要求另配 `OPENAI_API_KEY`。Python 3.10 通过条件依赖 `tomli` 读取配置，
较新 Python 使用标准库 `tomllib`，不需要因此更换应用运行环境。
`THERMOFLOW_CODEX_HOME` 指向服务账户已有的 Codex 目录，例如 `/data/yihongzhu/_zty/.codex`；
未设置则使用 `CODEX_HOME` 或该账户的默认目录。`THERMOFLOW_CODEX_EXECUTABLE` 可设置完整可执行路径，
以免 systemd 的 PATH 中找不到 `codex`。模型和提供方沿用该目录的配置，不被 `OPENAI_MODEL` 替换。

不要把 `.codex`、登录凭据或密钥复制进代码仓库、发布包或容器镜像。服务账户必须能够使用自己的
Codex 登录、读取配置并连接已配置的提供方；初始化及凭据刷新可能还需要账户自己的目录可写。
现有 Docker 镜像不包含 Codex CLI，这个模式不能仅修改变量就完成容器部署。
以前受限会话在初始化阶段遇到过只读文件系统错误。2026-09-07 在 yihongzhu 服务器的
完整草案调用仍出现超时；按用户要求暂停在线联调，尚未完成工作流验收。
当前状态与恢复检查顺序见 [Agent 排障说明](CODEX_HARNESS_SETUP.md)，不要将历史权限报错当作当前诊断。

`THERMOFLOW_CODEX_TIMEOUT_SECONDS` 默认 120，范围 5-600 秒。每个 API 进程同一时间只接受一次
Codex 请求，超时终止并回收整个子进程组；这不是跨实例限流或费用硬上限。调用使用临时工作目录、
严格输出 Schema、只读沙箱和 ephemeral 会话，关闭 shell、MCP、插件、hooks、浏览器和子 Agent 功能。
不会改写共享配置，不读取或复制 `auth.json`。正常用户界面不展示 CLI 日志、认证信息或配置路径。
临时输出和状态文件会清理，但外部模型服务的数据保留政策仍由 Codex 所选提供方决定。
所有草案仍需用户检查并确认，模型失败不自动切换到离线规则或执行求解。

## 验证标准

1. `GET /health` 返回 `status=ok`、`compute.ready=true` 和 A800 设备名。
2. 上传一个 STL 并求解后，结果的 `compute_backend` 以 `cuda-cupy-` 开头。
3. `compute_device` 为 `NVIDIA A800 80GB PCIe`，能量误差仍低于方案阈值。
4. systemd 服务重启后，历史工件、研究和 VTK 文件仍从共享数据目录读取。

## 后台计算与恢复

工作台通过 `POST /v1/studies/{id}/tasks` 提交计算，接口返回任务后即可继续操作。
阶段、进度、耗时和取消入口在属性面板显示；刷新页面会重新读取服务端任务记录。
取消和超时会终止对应子进程，已确认的输入保留，可重新提交。任务保存结果的短暂阶段
不接受取消；若在此阶段超时或服务器中断，未完成发布的结果不会作为完成结果开放。

每个数据目录只允许一个 API 服务进程，不要对该目录启用多 Uvicorn/Gunicorn worker。
调度器通过文件锁拒绝重复实例。数值子进程用以下环境变量控制：

| 配置 | 默认值 | 范围与含义 |
| --- | --- | --- |
| `THERMOFLOW_TASK_WORKERS` | 1 | 1-4 个同时计算的独立进程 |
| `THERMOFLOW_TASK_QUEUE_LIMIT` | 8 | 0-100 个等待任务，不含计算中的任务 |
| `THERMOFLOW_TASK_TIMEOUT_SECONDS` | 900 | 1-7200 秒，从启动计算进程计时，不含排队 |
| `THERMOFLOW_RETAIN_MODELING_HISTORY` | true | true 保留最近 40 条建模对话；false 仅在当前页面会话中保留新对话 |

对话保留设置不影响结构化草案、待应用建议、研究输入或确认记录。模型仅接收最近 20 条对话及当前草案摘要；
关闭保留不会主动清除其他既有研究或备份。当前同研究并发请求有互斥保护，全实例 GPT 限流和保留到期清理仍需补齐。

`apply_and_solve` 的网格与求解共用一个超时预算；网格风险需要确认时任务以
`needs_review` 结束。用户确认非阻断风险后提交新的 `solve` 任务。
服务重启后，排队任务继续等待执行，已中断任务不会自动重新求解，也不恢复数值迭代。

并发、网格规模和超时限制不是操作系统内存硬配额。生产环境仍应配置服务级内存/CPU
隔离，并根据 GPU 显存设置并发，避免多个进程竞争同一 GPU。遗留同步求解接口及
可选 Agent 优化尚未纳入此调度器，不具备这套取消和超时控制，仅限受信访问。
当前平台尚未实现完整用户鉴权与项目隔离，不应直接公开到互联网。

增加部署验收：提交任务后刷新页面可恢复进度；取消后进程结束且输入保留；人为设置
较短超时可得到可操作提示；服务重启后可重新提交中断研究。诊断文件保留短编号、异常
类型及代码位置，不保存异常原文或绝对路径；诊断包下载和权限分级尚待实现。
