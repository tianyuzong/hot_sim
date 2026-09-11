# GLM 热仿真参数填写 Agent

在工作台选择一个未确认的研究，使用最右侧的“参数 Agent”侧边栏。侧栏可收起，收起后点击页面右边缘的“Agent”按钮展开。用自然语言描述材料、
温度、边界、热源、网格或瞬态时间，助手会显示修改前后值，并追问缺失信息。
点击“应用到草案”才写入表单；可撤销，复核材料与输入后再启动仿真。
只含追问的回复不生成空建议，可直接继续回答。已确认研究需先复制为新草案。

示例：

- “使用材料目录中的 6061-T6 铝合金，初始和环境温度 25 ℃。”
- “持续加热两分钟，时间步一秒，把第一个热源功率改为 20 W，位置保持不变。”
- “最高温度不能超过 80 ℃；其他设置保持不变。”
- “请检查当前草案，还缺哪些参数？”

## 服务器配置

在项目 `.env` 中配置：

```dotenv
THERMOFLOW_MODELING_PROVIDER=glm
ZHIPU_API_KEY=
THERMOFLOW_GLM_MODEL=glm-5.2
THERMOFLOW_GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
THERMOFLOW_GLM_TIMEOUT_SECONDS=60
```

`ZHIPU_API_KEY` 填写智谱开放平台的 API Key，也支持 `ZAI_API_KEY`。客户端导出的
`enc:v1:` OAuth 登录凭据不能直接调用模型 API。密钥只在服务端读取，不返回给前端。
首次写入 `.env` 的密钥不需要重启，点击 Agent 中的“重新检查”即可检测；如果密钥由
进程环境变量提供，则环境变量优先，更新它需要重启服务。模型名、地址和 provider 的
修改也需要重启服务。可以按账号可用模型修改 `THERMOFLOW_GLM_MODEL`。

`/health` 的 `modeling_agent.configured` 仅表示配置存在，不代表额度和远程服务已验证。
未配置时手动编辑和求解仍可用。认证失败、限流、超时和格式错误均保留原草案，
不会回退到其他模型并冒充 GLM。真实模型质量需在有效 Key 到位后进行联调。

此 Agent 与原有创建研究规划器、结果优化 Agent 分开配置；
`THERMOFLOW_MODELING_PROVIDER=inherit` 可恢复使用现有规划器处理建模对话。

## 实现与验证

GLM 通过 Chat Completions JSON 模式返回稀疏参数修改；Pydantic 校验结构与数值，
材料必须引用服务端目录，组件和区域继续经过现有工程策略校验。已保存草案是基线；
服务端版本锁阻止迟到回复覆盖新的表单修改。Agent 不能确认输入或触发求解。
测试使用隔离 HTTP 响应，不会向模型服务发送真实工程或消耗额度：

```bash
./.venv312/bin/python -m pytest tests/test_glm_modeling.py tests/test_modeling.py tests/test_model_failures.py
```

接口依据：[智谱 OpenAI 兼容接口](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction)、
[JSON 结构化输出](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)。
