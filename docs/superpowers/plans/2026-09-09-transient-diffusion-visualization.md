# 瞬态热扩散可视化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让瞬态结果用真实的早期密集求解帧清楚呈现“热源附近先热、随后向外扩散”，同时保留固定绝对温标、顺滑本地播放和材料超温告警。

**Architecture:** 求解端基于实际体素间距和最大材料热扩散率生成最多 201 帧的非均匀输出时间表；后向欧拉在每个输出间隔内按不超过用户 `time_step_s` 的真实子步推进，只保存输出时刻。前端继续一次加载全部绝对温度帧，并在新的默认“热扩散”模式中只派生 `T - Tmin(frame)` 着色标量，使用全时段固定空间温差色标；“绝对温度”模式继续使用全时段固定绝对温标。结果页从已保存的材料有效温区和全时段温度范围生成显著告警。

**Tech Stack:** Python 3.12、NumPy、SciPy/CuPy 稀疏求解、FastAPI/Pydantic、原生 JavaScript、Three.js、pytest、Node.js、Playwright。

**Spec:** `docs/superpowers/specs/2026-09-09-transient-diffusion-visualization-design.md`

## Global Constraints

- 所有播放帧都必须由后向欧拉真实推进得到；不得插值、复制或由前端伪造中间温度场。
- `time_step_s` 是最大内部积分步长，任何内部 `dt` 都不能超过它。
- 输出包含 `t=0` 和精确的 `duration_s`，严格递增，总帧数不超过 201。
- 前端不得改写批量播放接口返回的绝对温度数组。
- 热扩散与绝对温度两种模式的三个色标数值在拖动和播放中都固定不变。
- 稳态、热流、等值带、剖面、研究差值以及旧的均匀时间结果保持兼容。
- 当前工作副本没有 `.git` 元数据，不初始化新仓库；每项任务完成后用测试结果和 `shasum -a 256` 记录检查点，跳过不可执行的 Git 提交步骤。
- 生产部署目录为 Firework 的 `/data/yihongzhu/_zty/hot_sim`；部署前确认没有运行中的求解任务，并保留服务器数据目录。

---

## Task 1: 生成受材料扩散率约束的输出时间表

**Files:**

- Create: `src/thermoflow/solvers/time_grid.py`
- Create: `tests/test_transient_time_grid.py`

- [x] **Step 1: 写失败测试，锁定时间表不变量**

  在 `tests/test_transient_time_grid.py` 覆盖：

  ```python
  def test_adaptive_grid_resolves_early_copper_diffusion():
      grid = build_transient_time_grid(
          duration_s=60.0,
          maximum_step_s=1.0,
          pitch_m=0.002,
          diffusivities_m2_s=[391.0 / (8940.0 * 385.0)],
      )
      assert grid.output_times_s[0] == 0.0
      assert grid.output_times_s[-1] == 60.0
      assert len(grid.output_times_s) == 201
      assert grid.early_step_s is not None
      assert grid.output_times_s[1] <= grid.early_step_s * (1 + 1e-12)
      assert all(b > a for a, b in pairwise(grid.output_times_s))
      assert grid.adaptive is True
  ```

  另测均匀分支、无效扩散率回退、指数限制 `[1, 4]`、子步总数估算及所有相邻时间严格递增。

- [x] **Step 2: 运行测试并确认 RED**

  Run: `./.venv/bin/python -m pytest tests/test_transient_time_grid.py -q`

  Expected: 因 `thermoflow.solvers.time_grid` 不存在而失败。

- [x] **Step 3: 实现纯函数时间表模块**

  在 `src/thermoflow/solvers/time_grid.py` 提供稳定接口：

  ```python
  @dataclass(frozen=True)
  class TransientTimeGrid:
      output_times_s: tuple[float, ...]
      early_step_s: float | None
      exponent: float
      adaptive: bool
      fallback_reason: str | None
      estimated_integration_steps: int

  def build_transient_time_grid(
      *,
      duration_s: float,
      maximum_step_s: float,
      pitch_m: float,
      diffusivities_m2_s: Sequence[float],
      output_intervals: int = 200,
  ) -> TransientTimeGrid:
      """返回包含 0 和最终时刻的真实输出时间表。"""

  def integration_targets(
      start_s: float,
      end_s: float,
      maximum_step_s: float,
  ) -> tuple[float, ...]:
      """返回严格递增且末项精确为 end_s 的内部推进目标。"""
  ```

  具体规则：`alpha_max=max(valid alpha)`、`t_cell=pitch_m**2/alpha_max`、`early=min(maximum_step_s, 0.25*t_cell)`；均匀首步已足够小时 `p=1`，否则用 `log(early/duration)/log(1/N)` 并夹到 `[1,4]`。用显式赋值保证第一个和最后一个值精确为 `0.0`、`duration_s`，再校验有限、严格递增。没有有效扩散率时使用 `N=ceil(duration/maximum_step)` 的旧式均匀输出，最多 200 个区间，并填写中文 `fallback_reason`。

- [x] **Step 4: 运行单元测试并确认 GREEN**

  Run: `./.venv/bin/python -m pytest tests/test_transient_time_grid.py -q`

  Expected: PASS。

- [x] **Step 5: 记录非 Git 检查点**

  Run: `shasum -a 256 src/thermoflow/solvers/time_grid.py tests/test_transient_time_grid.py`

---

## Task 2: 让后向欧拉支持非均匀输出和受限内部子步

**Files:**

- Modify: `src/thermoflow/solvers/transient.py`
- Modify: `tests/test_transient.py`

- [x] **Step 1: 先写非均匀积分失败测试**

  给 `tests/test_transient.py` 增加：

  ```python
  steps = list(integrate_thermal_system(
      matrix, rhs, capacity, initial,
      duration_s=1.0,
      time_step_s=0.2,
      output_times_s=(0.05, 0.3, 1.0),
      relative_tolerance=1e-10,
      max_iterations=100,
      preference="cpu",
      integration_steps=recorded_steps,
  ))
  assert [step[0] for step in steps] == pytest.approx([0.05, 0.3, 1.0])
  assert max(recorded_steps) <= 0.2
  assert sum(step[3] for step in steps) == pytest.approx(
      capacity[0] * (steps[-1][1][0] - initial[0])
  )
  ```

  同时测试重复/倒序/越界输出时刻会抛出 `ValueError`，旧调用不传 `output_times_s` 时仍返回原均匀时刻。

- [x] **Step 2: 运行测试并确认 RED**

  Run: `./.venv/bin/python -m pytest tests/test_transient.py -q`

  Expected: 新参数尚不存在而失败。

- [x] **Step 3: 扩展积分器但保持旧接口兼容**

  在现有关键字参数中增加：

  ```python
  output_times_s: Sequence[float] | None = None,
  integration_steps: list[float] | None = None,
  ```

  `output_times_s is None` 时继续生成旧式均匀输出。指定输出时刻时，对每个目标调用 `integration_targets(previous_time, target, time_step_s)`；每个内部时刻都重建 `C/dt` 并调用现有 CPU/CUDA 稀疏求解。一个输出区间内累计 `energy_change`，对能量误差和线性残差取最大值，只在目标输出时刻 `yield`。异常信息加入目标时刻和实际 `dt`，但不得吞掉底层后端诊断。

- [x] **Step 4: 验证解析冷却、能量闭合与旧均匀行为**

  Run: `./.venv/bin/python -m pytest tests/test_transient.py -q`

  Expected: PASS；现有一阶收敛断言仍成立。

- [x] **Step 5: 记录非 Git 检查点**

  Run: `shasum -a 256 src/thermoflow/solvers/transient.py tests/test_transient.py`

---

## Task 3: 将自适应时间表接入体素求解、策略和结果说明

**Files:**

- Modify: `src/thermoflow/solvers/voxel_stl.py`
- Modify: `src/thermoflow/policy.py`
- Modify: `tests/test_transient.py`
- Modify: `tests/test_compute.py`
- Modify: `tests/test_policy.py`

- [x] **Step 1: 写端到端数值失败测试**

  将瞬态接口测试从固定 `[0, 0.3, 0.6, 0.9, 1]` 改为检查不变量：201 帧、首尾时刻、严格递增、首帧小于铜/铝的 `0.25 h²/alpha`、播放帧与结果一一对应。新增局部点热源断言：第一个正时间帧的热点到热源映射中心不超过一个体素对角线，热点邻域温升大于远端温升。

- [x] **Step 2: 运行数值和策略测试并确认 RED**

  Run: `./.venv-runtime/bin/python -m pytest tests/test_transient.py tests/test_policy.py tests/test_compute.py -q`

  Expected: 当前求解器仍输出均匀帧，新增断言失败。

- [x] **Step 3: 在体素域建立时间表**

  在密度、比热字段建立后计算活动单元扩散率：

  ```python
  diffusivity = conductivity[active] / (density[active] * heat_capacity[active])
  time_grid = build_transient_time_grid(
      duration_s=plan.duration_s,
      maximum_step_s=plan.time_step_s,
      pitch_m=pitch_m,
      diffusivities_m2_s=diffusivity.tolist(),
  )
  ```

  将 `time_grid.output_times_s[1:]` 传入积分器。结果 `assumptions` 明确记录：自适应/回退状态、首个正时刻、输出帧数、最大积分步长和“每帧均为真实求解状态”。如发生回退，附上 `fallback_reason`。

- [x] **Step 4: 调整策略资源估算语义**

  保留 `MAX_TIME_STEPS=200` 作为最大保存区间数。`validate_plan` 中将 `ceil(duration/time_step)` 命名为 `maximum_step_intervals`，继续阻止过小的用户最大步长导致失控；`derived` 新增 `saved_time_steps=200` 和 `maximum_internal_step_s`。瞬态 cell-step 限制使用 `max(maximum_step_intervals, saved_time_steps) * cells`，警告文案改为“最大积分步长 + 自适应真实输出帧”。

- [x] **Step 5: 覆盖 CPU/CUDA 时间数组一致性**

  在 `tests/test_compute.py` 保留 CPU 求解和强制 CUDA 不静默回退的边界测试；真实 CPU/CUDA 时间数组与温度一致性在 Firework RTX 4090 部署验收中完成，不能模拟 GPU 成功。

- [x] **Step 6: 运行相关测试并确认 GREEN**

  Run: `./.venv-runtime/bin/python -m pytest tests/test_transient_time_grid.py tests/test_transient.py tests/test_policy.py tests/test_compute.py tests/test_stl_solver.py tests/test_thermal_assembly_regression.py -q`

  Expected: PASS，结果帧数不超过 Pydantic 的 201 帧限制。

- [x] **Step 7: 记录非 Git 检查点**

  Run: `shasum -a 256 src/thermoflow/solvers/voxel_stl.py src/thermoflow/policy.py tests/test_transient.py tests/test_policy.py`

---

## Task 4: 抽出前端热扩散尺度和材料温区判定

**Files:**

- Create: `src/thermoflow/web/assets/transient-display.js`
- Create: `tests/browser/transient_display.mjs`
- Modify: `src/thermoflow/web/index.html`

- [x] **Step 1: 写纯前端逻辑失败测试**

  用 Node `vm` 加载新脚本并测试：绝对数组不变、全时段空间跨度正确、每帧派生标量从 0 开始、近似等温容差分支、多组件材料去重，以及超出/缺失有效温区的中文告警。

  目标接口：

  ```javascript
  window.ThermoFlowTransient = {
    diffusionScale(frames, toleranceK = 1e-9),
    diffusionScalars(temperatureK, frameMinimumK),
    materialRangeWarnings(plan, result),
  };
  ```

- [x] **Step 2: 运行测试并确认 RED**

  Run: `node tests/browser/transient_display.mjs`

  Expected: 新脚本不存在而失败。

- [x] **Step 3: 实现无副作用辅助函数**

  `diffusionScale` 返回 `{low: 0, high, uniform, toleranceK}`；`high` 是所有帧 `temperature_max_k-temperature_min_k` 的最大值。`diffusionScalars` 必须返回新数组并校验有限值。`materialRangeWarnings` 返回结构化对象：

  ```javascript
  {
    materialName,
    componentNames,
    validMinimumK,
    validMaximumK,
    resultMinimumK,
    resultMaximumK,
    message,
  }
  ```

  `message` 必须包含“常物性外推”“未建模温度相关物性和相变”“不可直接作为工程结论”。

- [x] **Step 4: 在 HTML 中先加载辅助脚本并更新缓存版本**

  在 `source-editor-model.js` 与 `app.js` 之间加入：

  ```html
  <script src="/assets/transient-display.js?v=20260909-1" defer></script>
  ```

  同时把 `app.js` 缓存参数推进到 `v=20260909-1`。

- [x] **Step 5: 运行纯逻辑测试并确认 GREEN**

  Run: `node tests/browser/transient_display.mjs`

  Expected: PASS。

- [x] **Step 6: 记录非 Git 检查点**

  Run: `shasum -a 256 src/thermoflow/web/assets/transient-display.js tests/browser/transient_display.mjs src/thermoflow/web/index.html`

---

## Task 5: 增加默认热扩散模式、固定色标和材料告警 UI

**Files:**

- Modify: `src/thermoflow/web/index.html`
- Modify: `src/thermoflow/web/assets/app.js`
- Modify: `src/thermoflow/web/assets/viewport.mjs`
- Modify: `src/thermoflow/web/assets/styles.css`
- Modify: `tests/browser/transient.mjs`

- [x] **Step 1: 先扩展浏览器验收并确认 RED**

  在 `tests/browser/transient.mjs` 断言：

  - 瞬态结果首次打开时 `#diffusionViewButton[aria-pressed=true]`；
  - 标题包含 `空间温差 ΔT = T − 本帧最低温度 · 全时段固定`；
  - `#diffusionReference` 显示当前帧 `Tmin`，拖动后它变化；
  - 拖动 0、早期、中期、末态时三个色标文本完全相同；
  - 工件 WebGL 像素在早期帧发生局部变化，而不是整件同色；
  - 切换 `#thermalViewButton` 后标题为固定绝对温标，时间索引和网络请求数不变；
  - C110 超温结果显示 `#materialRangeWarning` 并包含有效温区和结果温区；
  - 0 K 空间跨度的夹具显示近似等温状态且不放大噪声。

- [x] **Step 2: 运行浏览器测试并确认 RED**

  Run: `node tests/browser/transient.mjs`

  Expected: `#diffusionViewButton` 和告警节点不存在而失败。

- [x] **Step 3: 增加清晰的界面节点**

  在可视化按钮组中将原 `温度场` 改名为 `绝对温度`，前面增加：

  ```html
  <button id="diffusionViewButton" type="button" aria-pressed="false" hidden>热扩散</button>
  ```

  在色标内增加 `<small id="diffusionReference" hidden></small>`；在结果标题下增加 `<div class="notice notice-warning material-range-warning" id="materialRangeWarning" role="alert" hidden></div>`。

- [x] **Step 4: 在 app 状态和播放缓存中计算一次固定尺度**

  `state.visualizationMode` 白名单加入 `diffusion`，新增 `diffusionScale`、`diffusionDefaultedStudyId`。批量 playback 完整校验成功后调用 `ThermoFlowTransient.diffusionScale(playback.frames)`；同一瞬态研究第一次载入且不是用户主动恢复其他结果模式时设为 `diffusion`。稳态或无时间帧时隐藏按钮并回退 `thermal`。

  `drawWorkpiece()` 在 `diffusion` 模式传递：

  ```javascript
  low: 0,
  high: state.diffusionScale.high,
  diffusion: true,
  diffusionReferenceK: playbackSurface.temperature_min_k,
  lockTemperatureScale: true,
  ```

  比较模式仍只允许现有 `thermal`。

- [x] **Step 5: 在 Three.js 重建阶段只替换着色标量**

  `viewport.mjs` 中先完成热源近似预览，再在 `options.mode === "diffusion"` 时执行：

  ```javascript
  const reference = Math.min(...scalars);
  scalars = options.diffusionUniform
    ? scalars.map(() => 0.5 * options.high)
    : scalars.map(value => value - reference);
  ```

  不修改 `surface.temperature_k`。legend 回调增加 `diffusion`、`referenceTemperatureK`、`uniform`。近似等温时用中性色，并由 `viewportStatus` 显示 `该结果在当前网格与容差下近似等温`。

- [x] **Step 6: 渲染固定图例、当前帧参考值和材料告警**

  热扩散标题固定，只有末尾时间可变化；三个刻度显示 `0 K / high/2 K / high K`。`diffusionReference` 显示 `当前帧参考 Tmin = ... K`。`renderResult` 使用整个时段 `temperature_min_over_time_k/temperature_max_over_time_k` 调用材料告警辅助函数，列出组件、材料、有效温区和结果温区。CSS 保证桌面与 390 px 页面不遮挡画布控制、图例和结果指标。

- [x] **Step 7: 更新 Three.js 与应用缓存参数**

  `app.js` 中 `viewport.mjs` 改为 `v=20260909-1`；后续若验证中再次修改资源，同一轮统一递增，不混用旧版本。

- [x] **Step 8: 运行前端逻辑和真实浏览器验收**

  Run: `node tests/browser/transient_display.mjs`

  Run: `node tests/browser/transient.mjs`

  Expected: PASS；快速拖动期间无 `/frames/{index}` 或 `/view` 请求，两个模式色标均固定。

- [x] **Step 9: 记录非 Git 检查点**

  Run: `shasum -a 256 src/thermoflow/web/index.html src/thermoflow/web/assets/app.js src/thermoflow/web/assets/viewport.mjs src/thermoflow/web/assets/styles.css tests/browser/transient.mjs`

---

## Task 6: 全量回归、中文说明、Firework 部署和真实 60 秒验收

**Files:**

- Modify: `USAGE_SUMMARY.md`
- Modify: `docs/IMPLEMENTATION_STATUS.md`
- Create: `docs/verification/transient-diffusion-20260909.md`
- Create: `docs/verification/transient-diffusion-desktop-20260909.png`
- Create: `docs/verification/transient-diffusion-mobile-20260909.png`

- [x] **Step 1: 运行静态与全量测试**

  Run: `./.venv/bin/python -m ruff check src tests`

  Run: `./.venv/bin/python -m pytest -q --tb=short`

  Run: `for test in tests/browser/*.mjs; do node "$test"; done`

  Expected: 全部 PASS；不得只报告局部测试。

  实际：Python 最终全量回归 174 项和全部浏览器/Node 验收通过；本批修改文件的 Ruff 定向检查通过。全仓库 Ruff 仍有 26 项本批次前已存在的导入/风格问题，已如实记录在验收报告中，未批量改写无关历史代码。

- [x] **Step 2: 更新中文使用说明**

  `USAGE_SUMMARY.md` 明确：瞬态默认“热扩散”、如何切换“绝对温度”、色标为什么固定、`time_step_s` 现在是最大积分步长、旧结果为何没有早期密集帧、材料超温告警的含义，以及热源在一次求解中仍固定。

- [x] **Step 3: 部署前检查 Firework 状态**

  Run: `/usr/bin/ssh -4 -p 2001 yihongzhu@114.80.86.187 'cd /data/yihongzhu/_zty/hot_sim && pgrep -af thermoflow && curl -fsS http://127.0.0.1:18080/health'`

  Expected: 识别唯一服务进程、健康检查成功；再通过任务接口确认没有 `running` 状态任务。不得删除 `data/`、`.env` 或远端虚拟环境。

- [x] **Step 4: 同步代码和静态资源**

  只同步本计划列出的生产文件、文档和测试；使用 `scp -4 -P 2001` 传入显式目标路径，不使用会删除远端文件的同步参数。远端运行 `./.venv312/bin/python -m pytest` 的数值相关子集后再重启。

- [x] **Step 5: 安全重启并验证 CUDA**

  终止已确认的唯一旧服务 PID，使用服务器现有环境变量和启动方式重新启动 `/data/yihongzhu/_zty/hot_sim/.venv312/bin/thermoflow`。随后验证：

  ```bash
  curl -fsS http://127.0.0.1:18080/health
  ```

  Expected: `status=ok`、`selected_backend=cuda-cupy`，并能读取既有研究和 `/playback`。

- [x] **Step 6: 复制现有 C110 算例并重新求解 60 秒**

  通过现有复制接口创建新研究，保留 20×10×5 mm、4 个热源、70 W、60 s、最大步长 1 s，不覆盖 `study-7db06558c9cd4a829c67c2930327dd87`。确认新结果：

  - 201 帧，首个正时刻满足铜的早期扩散尺度；
  - 最终时刻精确为 60 s；
  - `compute_backend=cuda-cupy-cg` 且设备为 RTX 4090；
  - 早期热点接近 40 W 主热源映射位置，近端温升先于远端；
  - playback 帧数、时刻、温度数组长度与结果一致；
  - 全时段最高温度触发 C110 有效温区告警。

- [x] **Step 7: 用 HTTP 页面做桌面和移动端最终演示**

  只打开 `http://127.0.0.1:18080/docs`，禁止用 `file://.../index.html`。在 1440×900 和 390×844 下截图并记录：默认热扩散视图、早期局部热点、末态扩散、固定三个图例数字、绝对温度切换和材料告警。

- [x] **Step 8: 写验收记录并做最终自审**

  `docs/verification/transient-diffusion-20260909.md` 记录研究 ID、材料、网格间距、扩散率、输出时刻摘要、近端/远端早期温升、热点位置、色标值、CUDA 后端、告警文字、测试命令和截图路径。逐条对照规格第 11 节六项完成标准，不留 `TODO`、`TBD`、`待验证`。

- [x] **Step 9: 最终非 Git 检查点**

  Run: `shasum -a 256 src/thermoflow/solvers/time_grid.py src/thermoflow/solvers/transient.py src/thermoflow/solvers/voxel_stl.py src/thermoflow/policy.py src/thermoflow/web/assets/transient-display.js src/thermoflow/web/assets/app.js src/thermoflow/web/assets/viewport.mjs src/thermoflow/web/index.html USAGE_SUMMARY.md`

  Expected: 将哈希写入验收记录；本工作副本不是 Git 仓库，因此不声称已提交或合并。

---

## Plan Self-Review

- 每一项规格目标均映射到数值、API/播放、UI、告警、兼容或部署测试。
- 时间表、积分器、前端辅助函数均有确定接口和失败测试，没有 `TODO/TBD`。
- `time_step_s` 在模型层保持字段兼容，只改变求解语义和界面说明；无需迁移旧 JSON。
- Pydantic `SimulationResult.time_steps` 的 `max_length=201` 与输出上限一致。
- 旧结果继续通过其既有 `time_s` 播放；热扩散尺度由实际播放帧计算，不要求新增 API 字段。
- 材料告警复用方案内已保存的 `ThermalMaterial`，不请求外部材料数据，不虚构温区。
- 非均匀时间积分仍经过现有 `solve_sparse_system`，因此 CPU/CUDA 后端边界保持不变。
- 计划没有引入移动热源轨迹、温度相关物性或相变，符合非目标。
