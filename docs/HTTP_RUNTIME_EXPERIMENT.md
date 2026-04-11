# HTTP Runtime 实验计划

这个分支用于验证一件事：

> 是否可以在不依赖 Playwright 的前提下，仅靠关键认证 cookie 走通千问转写主链路的 HTTP 请求。

当前分支：

- `experiment/http-runtime`

相关方法文档：

- [分析排查手册](./ANALYSIS_PLAYBOOK.md)

---

## 当前分支状态总览

这份文档除了记录实验，也承担当前分支的恢复手册作用。

### 当前已经确认的状态

- 默认 runtime 已切换为：
  - `http`
- 默认最小认证模式为：
  - `ticket-only`
- 在默认 `http` 模式下，主转写链路已经可以走分支内自己的模块：
  - `src/qwen_http_runtime/runtime.py`
  - `src/qwen_http_runtime/accounts.py`
  - `src/qwen_http_runtime/http.py`
  - `src/qwen_http_runtime/oss_upload.py`
  - `src/qwen_http_runtime/result_metadata.py`
  - `src/qwen_http_runtime/flow.py`
- 旧的 `qwen_web_capture.*` 仍保留在仓库中，但当前定位是：
  - legacy / fallback / 对照实现
  - 不再是默认依赖

### 当前已经完成的验证层级

- probe 级验证：已完成
- 干净 venv、无 Playwright 验证：已完成
- 正式 backend 代码路径验证：已完成
- 服务级 API 回归验证：已完成
- 默认 runtime 服务级回归验证：已完成

### 当前还没做的事

- 还没有把旧 Playwright 路径物理删除
- 还没有补齐 service 层自动化测试，覆盖 `runtime=http` 的真实 job 执行
- 还没有整理“迁移到主分支”的合并策略
- 还没有决定是否保留 dual-backend 结构，还是彻底收口为单 backend

### 当前分支的推荐认知

可以把它理解成：

> “HTTP runtime 已经成为默认主实现，Playwright 退化为遗留回退路径。”

而不是：

> “仓库里旧代码已经全部删除。”

---

## 重新测试清单

如果后续重新打开这个分支，建议按下面顺序恢复，不要直接跳步骤。

### 1. 先确认当前分支与工作区

```bash
git branch --show-current
git status --short
git log --oneline -3
```

预期：

- 当前分支应为 `experiment/http-runtime`
- 最近两个关键提交应能看到：
  - `5876407 feat: add http runtime experiment backend`
  - `e361d3c feat: make http runtime the default backend`

### 2. 先跑单元测试

```bash
cd /Users/gq/Projects/qwen2api
PYTHONPATH=src python -m unittest discover -s tests -v
```

### 3. 再跑干净 venv 验证

确认无 Playwright：

```bash
/tmp/qwen2api-no-playwright-test/bin/python - <<'PY'
import importlib.util
print(importlib.util.find_spec('playwright'))
PY
```

然后跑正式代码路径：

```bash
cd /Users/gq/Projects/qwen2api
PYTHONPATH=src QWEN_HTTP_COOKIE_MODE=ticket-only \
  /tmp/qwen2api-no-playwright-test/bin/python - <<'PY'
import asyncio
from pathlib import Path
from qwen2api.config import get_settings
from qwen2api.qwen_adapter import transcribe_via_qwen

async def main():
    get_settings.cache_clear()
    settings = get_settings()
    result = await transcribe_via_qwen(
        settings=settings,
        input_path=Path('/Volumes/GQ/63.打工人的法律必修课（完结）/7-当遭遇工伤与职业病之后.mp4'),
        output_dir=Path('/tmp/qwen2api-http-backend-retest-output'),
        export_format='md',
        delete_remote=True,
        account_id='',
        account_strategy='round-robin',
    )
    print(result)

asyncio.run(main())
PY
```

### 4. 最后跑服务级验证

启动服务：

```bash
cd /Users/gq/Projects/qwen2api
QWEN_HTTP_COOKIE_MODE=ticket-only PYTHONPATH=src \
  uvicorn qwen2api.main:app --host 127.0.0.1 --port 18002
```

另一个终端调用：

```bash
curl http://127.0.0.1:18002/health
curl -X POST http://127.0.0.1:18002/api/v1/transcriptions/local/async \
  -H 'Content-Type: application/json' \
  -d '{"path":"/Volumes/GQ/63.打工人的法律必修课（完结）/7-当遭遇工伤与职业病之后.mp4","format":"md"}'
curl http://127.0.0.1:18002/api/v1/jobs/<job_id>
curl -L http://127.0.0.1:18002/api/v1/jobs/<job_id>/file -o result.md
```

### 5. 失败时先排什么

优先按这个顺序排：

1. 当前 auth 文件是不是还是有效
2. `tongyi_sso_ticket` 是否还存在
3. 远端千问接口是否临时变更
4. OSS 上传是否返回异常
5. export 接口返回码是否变化

不要一上来就怀疑架构，先确认认证和远端行为有没有变。

---

## 后续优化顺序

如果继续优化整个项目，建议按下面顺序推进。

### P1：补 service 层自动化测试

当前自动化测试主要还是：

- 配置
- bundle 选择
- API 层基础路径

下一步最应该补的是：

- `runtime=http` 下的 job 执行路径测试
- 至少把 `transcribe_via_qwen` mock 成 http backend 风格，覆盖 service 层

### P2：明确 legacy 策略

需要做决策：

- 保留 `playwright` backend 多久
- 是保留为 fallback，还是准备删除

建议先保留，不要立刻删。

### P3：收口认证文件格式

当前已经证明最小认证 cookie 是：

- `tongyi_sso_ticket`

后面可以继续做：

- 设计轻量 auth 文件格式
- 不再要求完整 Playwright storage-state JSON
- 运行时优先读取最小 auth 格式，再兼容旧 state

### P4：从实验脚本抽离剩余辅助能力

当前很多验证逻辑已经落到了正式 backend，但仍然可以继续收口：

- 把更多 probe/diagnostic 能力抽成可复用 helper
- 为后续排查保留更好的调试入口

### P5：准备主分支合并策略

后续如果要把这个分支合到主线，建议先决定：

- 是直接切主分支默认 backend 到 `http`
- 还是先做一次预发布分支
- 是否需要新的 tag / release note

---

## 背景结论

前一轮隔离实验已经确认：

- 当前登录态真正起作用的是 **cookie**
- `origins/localStorage` 不是当前 API 链路的关键依赖
- 核心认证 cookie 是：
  - `tongyi_sso_ticket`

进一步实验结果：

- 只保留 `tongyi_sso_ticket` 仍能成功请求：
  - quota 接口
  - `record/oss/token/get`
- 只保留 `tongyi_sso_ticket_hash` 不够
- 只保留 `XSRF-TOKEN` 不够

这说明“认证材料最小化”是可行的。

---

## 当前阻塞点

虽然认证材料可以最小化，但当前实现仍然硬依赖：

- `playwright.async_api`

失败位置主要在：

- `src/qwen_web_capture/flow.py`
- `src/qwen_web_capture/quota.py`

所以现阶段问题已经不是“登录态不对”，而是：

> 主链路运行时还没从 Playwright request context 迁移到纯 HTTP 客户端。

---

## 分阶段目标

### Phase 1：验证纯 HTTP 请求可用

先不改主流程，只做探针验证。

- [x] quota
- [x] `record/oss/token/get`
- [x] 隔离实验确认 `origins/localStorage` 不是关键因素
- [x] 隔离实验确认 `tongyi_sso_ticket` 是核心认证 cookie

落地脚本：

- `scripts/http_runtime_probe.py`

已验证结果：

- `ticket-only` 模式下，仅发送 `tongyi_sso_ticket`
- quota 可成功返回
- `record/oss/token/get` 可成功返回 `genRecordId` / `recordId`

---

### Phase 2：继续向主流程推进

这一阶段先不正式抽 backend，先继续按“逐步验证”往下走：

- [x] `upload_heartbeat`
- [x] `record/start`
- [x] `poll_record`
- [x] `export_record`
- [x] `record/read`
- [x] `record/delete`

这里的目标是先证明：

> 不依赖 Playwright，也能完成上传后的完整业务闭环。

落地方式：

- 在 `scripts/http_runtime_probe.py` 里新增 `--action=start`
- 直接使用纯 HTTP + OSS 上传 helper 做上传、heartbeat、start 验证

通过标准：

- 能拿到 `recordId`
- 能拿到 `genRecordId`
- 能拿到 `batchId`

当前验证结果：

- 已使用 `ticket-only` 模式验证成功
- 已在**不依赖 Playwright** 的脚本里完成：
  - OSS token 获取
  - OSS multipart upload
  - `upload_heartbeat`
  - `record/start`
  - `poll_record`
  - `record/read`
  - `export_record`
  - `record/delete`
- 已拿到：
  - `recordId`
  - `genRecordId`
  - `batchId`
- 已成功下载 Markdown 成品到本地

---

### Phase 3：把请求链路抽成纯 HTTP backend

当 Phase 1 / 2 已证明稳定后，再正式抽以下接口：

- `get_quota_snapshot`
- `get_oss_token`
- `upload_heartbeat`
- `start_record`
- `poll_record`
- `export_record`

这一步应该新增一套独立 backend，而不是立刻替换主实现。

建议目录：

- `src/qwen_http_runtime/`

或后续统一收口成：

- `src/qwen_runtime/http_backend.py`

---

### Phase 3：并行对照验证

在不动现有 API 层的前提下：

- Playwright backend 保留
- HTTP backend 新增
- 通过配置切换

建议切换变量：

- `QWEN2API_RUNTIME=playwright|http`

---

## 当前实验边界

这个分支暂时**不要**做：

- 改 API 路由
- 改 job queue
- 改 batch/report
- 改发布分支
- 改既有 `v0.1.0`

只允许做：

- 探针脚本
- 认证材料分析
- HTTP backend 原型
- 小范围实验文档

---

## 下一步推荐顺序

1. 用 `scripts/http_runtime_probe.py` 固化 quota / token / start 探针
2. 新增一个最小 cookie 文件格式约定
3. 再实现纯 HTTP 版 quota / token / heartbeat / start / poll / export / delete
4. 做一个最小 backend 接口抽象，避免 API 层直接绑定 Playwright
5. 最后才考虑替换主 runtime

---

## 实验记录

### 2026-04-10：认证材料最小化实验

隔离目录：

- `/tmp/qwen2api-auth-analysis`

关键结果：

- `full.json`：成功
- `cookie_only.json`：成功
- `qianwen_only.json`：成功
- `auth_focus.json`：成功
- `ticket_only.json`：成功
- `origins_only.json`：失败

进一步缩小：

- `ticket_main_only.json`（仅 `tongyi_sso_ticket`）：成功
- `ticket_hash_only.json`：失败
- `xsrf_only.json`：失败
- `ticket_plus_xsrf.json`：成功

结论：

- 真正关键的是 `tongyi_sso_ticket`
- `origins/localStorage` 不是当前 API 链路的必要条件

### 2026-04-10：无 Playwright 环境验证

隔离环境：

- `/tmp/qwen2api-no-playwright-test`

结果：

- 当前主链路在无 `playwright` 环境下会直接失败
- 失败点是导入：
  - `src/qwen_web_capture/flow.py`
  - `from playwright.async_api import async_playwright`

结论：

- 当前项目还没有去 Playwright 化
- 但认证与部分业务接口已经证明可以纯 HTTP 执行

### 2026-04-10：纯 HTTP 上传与启动验证

验证文件：

- `/Volumes/GQ/63.打工人的法律必修课（完结）/7-当遭遇工伤与职业病之后.mp4`

验证方式：

- 使用 `scripts/http_runtime_probe.py`
- 模式：`ticket-only`
- 动作：`start`
- 不使用 Playwright

验证结果：

- quota：成功
- `record/oss/token/get`：成功
- OSS multipart upload：成功
- `upload_heartbeat`：成功
- `record/start`：成功

关键结果：

- `record_id = 3c31149d-9742-4176-b665-1c61bac7d573`
- `gen_record_id = ro84nrlr8o8xnkb3`
- `batch_id = 13bdb429-f0b7-4d03-9ddc-72bce6b992c1`

补充说明：

- 本次上传共 129 个分片
- 说明“认证 + 上传 + 起跑”这一段已经可以完全脱离 Playwright
- 下一步重点转到：
  - `poll_record`
  - `export_record`

### 2026-04-11：纯 HTTP 完整链路验证

验证文件：

- `/Volumes/GQ/63.打工人的法律必修课（完结）/7-当遭遇工伤与职业病之后.mp4`

验证方式：

- 使用 `scripts/http_runtime_probe.py`
- 模式：`ticket-only`
- 动作：`full`
- 不使用 Playwright

完整结果：

- quota：成功
- `record/oss/token/get`：成功
- OSS multipart upload：成功
- `upload_heartbeat`：成功
- `record/start`：成功
- `poll_record`：成功
- `record/read`：成功
- `export_record`：成功
- Markdown 下载：成功
- `record/delete`：成功

关键结果：

- `record_id = cdf6a7a5-6b7b-4003-85e3-a40cfb65b89c`
- `gen_record_id = 372e9ovmk2aanxb6`
- `batch_id = 1739d24b-ff40-4644-8702-d06f49e33b03`
- `export_task_id = b421b04e81234e57b201aa691581673a`
- `output_path = /private/tmp/qwen2api-http-runtime-output/372e9ovmk2aanxb6.md`

结论：

- 在当前验证范围内，`tongyi_sso_ticket` 一枚 cookie 已足够支撑整条主链路
- “认证 + 上传 + 起跑 + 轮询 + 导出 + 删除” 已经全部证明可以纯 HTTP 完成
- 下一步不再是可行性验证，而是工程化抽象和主 runtime 替换

### 2026-04-11：干净 venv（无 Playwright）复验

隔离环境：

- `/tmp/qwen2api-no-playwright-test`

确认结果：

- `playwright_spec = None`

验证方式：

- 使用该 venv 的 Python 直接运行：
  - `scripts/http_runtime_probe.py`
- 模式：`ticket-only`
- 动作：`full`
- 目标文件：
  - `/Volumes/GQ/63.打工人的法律必修课（完结）/7-当遭遇工伤与职业病之后.mp4`

完整结果：

- quota：成功
- `record/oss/token/get`：成功
- OSS multipart upload：成功
- `upload_heartbeat`：成功
- `record/start`：成功
- `poll_record`：成功
- `record/read`：成功
- `export_record`：成功
- Markdown 下载：成功
- `record/delete`：成功

关键结果：

- `record_id = 972aa7cd-ecb5-42e0-9e1d-02baf436700e`
- `gen_record_id = mloynm8m5382qagp`
- `batch_id = 24d1b67e-7fc2-4b5d-a525-70c47421989c`
- `export_task_id = c69794d64efb4864a24122f0fe9b5cac`
- `output_path = /private/tmp/qwen2api-http-runtime-output-cleanvenv/mloynm8m5382qagp.md`

严格结论：

- **在干净的、无 Playwright 的 Python 虚拟环境中，纯 HTTP 链路仍然可以跑完整个主流程**
- 当前仍然依赖的不是 Playwright，而是：
  - 有效的认证 cookie（已验证核心为 `tongyi_sso_ticket`）
  - 远端千问/OSS 网络可用

### 2026-04-11：正式 backend 代码路径验证

验证方式：

- 不是 probe 脚本
- 直接使用当前分支里的正式代码路径：
  - `qwen2api.qwen_adapter.transcribe_via_qwen`
  - `QWEN2API_RUNTIME=http`
  - `QWEN_HTTP_COOKIE_MODE=ticket-only`
- 运行环境仍然是：
  - `/tmp/qwen2api-no-playwright-test`
  - 无 Playwright

验证文件：

- `/Volumes/GQ/63.打工人的法律必修课（完结）/7-当遭遇工伤与职业病之后.mp4`

结果：

- `record_id = 99c357e5-449c-4fc8-886e-76958c087b0d`
- `gen_record_id = p7g395m8v6z6qz65`
- `remote_deleted = True`
- `export_path = /private/tmp/qwen2api-http-backend-formal-output/7-当遭遇工伤与职业病之后-2026-04-11T05-04-21+00-00.md`
- 输出文件存在，大小：
  - `15827`

结论：

- 当前分支中的**正式 HTTP backend 代码**已经不是概念验证，而是可以实际替代 Playwright 主链路运行
- 下一步工作重心应转到：
  - backend 配置暴露
  - 服务级真实回归
  - 逐步把默认 backend 从 `playwright` 切到 `http`

### 2026-04-11：服务级回归验证（HTTP backend）

启动方式：

- `QWEN2API_RUNTIME=http`
- `QWEN_HTTP_COOKIE_MODE=ticket-only`
- `PYTHONPATH=src uvicorn qwen2api.main:app --host 127.0.0.1 --port 18001`

验证文件：

- `/Volumes/GQ/63.打工人的法律必修课（完结）/7-当遭遇工伤与职业病之后.mp4`

验证接口：

- `GET /health`
- `POST /api/v1/transcriptions/local/async`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/file`

关键结果：

- `job_id = job_20260411T051302Z_1ffa75f3`
- `status = succeeded`
- `record_id = e5051b33-e168-4789-950c-54889ec374f6`
- `gen_record_id = 2yjoqzamjor3n68l`
- `output_file = /Users/gq/Projects/qwen2api/data/outputs/7-当遭遇工伤与职业病之后.md`
- `remote_deleted = true`

下载接口验证：

- `GET /api/v1/jobs/job_20260411T051302Z_1ffa75f3/file` 返回 `200`
- 下载文件与落盘文件 SHA256 一致
- 下载文件与落盘文件字节数一致：
  - `15827`

结论：

- 当前分支不仅 backend 函数可用
- **整个 qwen2api 服务在 HTTP backend 模式下也已真实跑通**
- 到这一步，HTTP runtime 已经具备进入默认化/替换评估的条件

### 2026-04-11：默认 runtime 服务级回归

启动方式：

- 不显式设置 `QWEN2API_RUNTIME`
- 仅设置：
  - `QWEN_HTTP_COOKIE_MODE=ticket-only`
- 启动命令：
  - `PYTHONPATH=src uvicorn qwen2api.main:app --host 127.0.0.1 --port 18002`

说明：

- 这一步用于证明“当前分支默认值已经切到 HTTP runtime”
- 不是靠额外设置 `QWEN2API_RUNTIME=http` 才能成功

验证接口：

- `GET /health`
- `POST /api/v1/transcriptions/local/async`
- `GET /api/v1/jobs/{job_id}`

关键结果：

- `job_id = job_20260411T060414Z_b105ac81`
- `status = succeeded`
- `record_id = 3b62de2f-e10e-4f04-882b-a43f26c42848`
- `gen_record_id = klrbn2mw4yd895zy`
- `output_file = /Users/gq/Projects/qwen2api/data/outputs/7-当遭遇工伤与职业病之后-2.md`

结论：

- 当前分支默认 runtime 已经可以直接作为服务的默认运行方案
- 到这一步，HTTP runtime 不仅“可以用”，而且已经“默认可用”

### 2026-04-11：HTTP backend 模块收口

这一轮继续把 HTTP runtime 从“可运行”推进到“自有模块闭环”：

- 已新增：
  - `src/qwen_http_runtime/runtime.py`
  - `src/qwen_http_runtime/accounts.py`
  - `src/qwen_http_runtime/http.py`
  - `src/qwen_http_runtime/oss_upload.py`
  - `src/qwen_http_runtime/result_metadata.py`
- 已切换：
  - `qwen_adapter` 在 `runtime_backend=http` 时加载 `qwen_http_runtime.*`
  - `qwen_http_runtime/flow.py` 不再依赖旧的 `qwen_web_capture.http`
  - `qwen_http_runtime/flow.py` 不再依赖旧的 `qwen_web_capture.oss_upload`
  - `qwen_http_runtime/flow.py` 不再依赖旧的 `qwen_web_capture.runtime`

保留情况：

- `qwen_web_capture.*` 仍然保留在分支里，作为 legacy / playwright backend 回退路径
- 但在默认 `http` 模式下，主链路已经不再依赖这些旧 helper 模块

验证结果：

- 单元测试通过
- 干净 venv、无 Playwright 环境下，正式 `transcribe_via_qwen -> http backend` 代码路径再次成功：
  - `record_id = b6c0dfc5-6c6c-437c-8e83-163479d455ab`
  - `gen_record_id = r28pn728rx3wq5mz`
  - `remote_deleted = True`
  - `export_path = /private/tmp/qwen2api-http-backend-selfcontained-output/7-当遭遇工伤与职业病之后-2026-04-11T13-11-43+00-00.md`

结论：

- 当前分支在默认 HTTP 模式下，已经可以认为“主转写链路走的是分支内自己的程序调用”
- 还保留旧 Playwright 路径，只是为了回退和对照，不再是默认依赖

### 2026-04-11：默认化决策

当前分支决定：

- `QWEN2API_RUNTIME` 默认值改成 `http`
- `QWEN_HTTP_COOKIE_MODE` 默认值保留 `ticket-only`
- Playwright backend 暂不删除，保留为回退路径
- `scripts/login_qwen.py` 继续保留，用于首次登录和 state 采集

原因：

- probe 级验证：已通过
- 正式 backend 验证：已通过
- 服务级回归：已通过
- 无 Playwright 干净 venv：已通过

结论：

- 当前分支已经具备“默认走 HTTP runtime”的条件

---

## 当前判断

现在**不需要新开项目**。

原因：

- 上层 API / job / batch / docs / release 已经稳定
- 真正要替换的是底层 runtime

所以这应该是：

- **当前项目内的 runtime 重构**

而不是：

- **重开一个新项目**
