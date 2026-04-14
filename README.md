# qwen2api

`qwen2api` 是一个独立发布的 HTTP API 服务，用来把通义千问「音视频速读」能力包装成稳定的本地/私有接口。

项目内置执行链路：

- `src/qwen2api/`：API、队列、任务存储、批次视图、重试、报告
- `src/qwen_http_runtime/`：当前默认执行链路，负责纯 HTTP 上传、轮询、导出
- `src/qwen_web_capture/`：legacy / fallback 浏览器态执行链路，主要用于兼容和回退

补充文档：

- [新手快速上手](./docs/QUICKSTART.md)
- [API 文档](./docs/API.md)
- [运维文档](./docs/OPERATIONS.md)
- [分析排查手册](./docs/ANALYSIS_PLAYBOOK.md)

## 设计约束

当前版本只产出 Markdown：

- 接口层只接受 `md` / `markdown`
- 返回体中的 `format` 永远是 `md`
- 下载接口只返回 Markdown 文件
- 最终成品统一输出到 `data/outputs/`

这样可以保证后续发布链路只处理一种输出格式。

## 当前接口

- `GET /health`
- `POST /api/v1/transcriptions`
- `POST /api/v1/transcriptions/local`
- `POST /api/v1/transcriptions/async`
- `POST /api/v1/transcriptions/local/async`
- `POST /api/v1/transcriptions/batch`
- `POST /api/v1/transcriptions/local/batch`
- `GET /api/v1/batches/{batch_id}`
- `GET /api/v1/batches/{batch_id}/report`
- `GET /api/v1/batches/{batch_id}/retry-candidates`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/file`

## 当前能力

- 单文件上传 / 本地路径提交
- 同步 / 异步 / 批量异步转写
- 账号池与账号策略
- 进程内持久队列与重启恢复
- 失败自动重试
- 批次报告与失败重试候选
- 可选 API Key 校验

## 项目目录

```text
qwen2api/
├── src/
│   ├── qwen2api/
│   └── qwen_web_capture/
├── docs/
├── scripts/
├── tests/
├── data/
├── .env.example
├── accounts.example.json
└── README.md
```

运行时目录说明：

- `data/jobs/<job_id>/`：单任务工作目录
- `data/outputs/`：最终 Markdown 成品
- `data/runtime/`：批次视图、账号状态、quota 状态
- `.auth/`：登录态文件目录，默认不纳入版本控制
- `accounts.json`：多账号配置，默认不纳入版本控制

## 快速开始

### 1. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

说明：

- 当前分支默认 runtime 是 `http`
- 日常转写主链路默认不再依赖 Playwright
- 这里保留 `playwright install chromium`，主要是为了首次登录脚本 `scripts/login_qwen.py`
- 如果你只使用已有登录态文件，不重新登录，可以暂时不执行这一步

### 2. 准备配置

```bash
cp .env.example .env
```

默认情况下：

- 服务配置和执行链路配置共用同一个 `.env`
- 单账号登录态默认读取 `.auth/qwen-storage-state.json`
- 多账号时可复制 `accounts.example.json` 为 `accounts.json` 后再修改
- 默认使用 `http` runtime
- 默认只发送最小认证 cookie：`tongyi_sso_ticket`

关键配置项：

- `QWEN2API_API_KEY`：可选接口鉴权
- `QWEN2API_RUNTIME`：当前分支默认是 `http`
- `QWEN_HTTP_COOKIE_MODE`：当前默认是 `ticket-only`
- `QWEN2API_DELETE_REMOTE`：转写完成后是否删除远端记录
- `QWEN2API_QWEN_AUTH_STATE`：单账号默认登录态
- `QWEN2API_QWEN_ACCOUNTS_FILE`：多账号池配置
- `QWEN2API_QWEN_DOTENV`：执行链路读取的环境变量文件
- `QWEN2API_QWEN_ROOT`：可选；仅在你要切换到其他执行链路 checkout 时覆盖

如果你要切回旧的 Playwright backend，可以显式设置：

```bash
QWEN2API_RUNTIME=playwright
```

如果你使用多账号，建议补一份配置：

```bash
cp accounts.example.json accounts.json
```

首次使用前，先生成登录态：

```bash
PYTHONPATH=src python scripts/login_qwen.py --out .auth/qwen-storage-state.json
```

如果是多账号，就分别执行：

```bash
PYTHONPATH=src python scripts/login_qwen.py --out .auth/account-1.json
PYTHONPATH=src python scripts/login_qwen.py --out .auth/account-2.json
```

### 3. 启动服务

```bash
PYTHONPATH=src uvicorn qwen2api.main:app --host 0.0.0.0 --port 18000 --reload
```

## 接口示例

### 健康检查

```bash
curl http://127.0.0.1:18000/health
```

### 同步转写

```bash
curl -X POST http://127.0.0.1:18000/api/v1/transcriptions \
  -F 'file=@/absolute/path/to/demo.mp4' \
  -F 'format=md'
```

### 本地路径异步转写

```bash
curl -X POST http://127.0.0.1:18000/api/v1/transcriptions/local/async \
  -H 'Content-Type: application/json' \
  -d '{
    "path": "/absolute/path/to/demo.mp4",
    "format": "md"
  }'
```

这里的 `path` 必须是**服务所在机器**上可直接访问的绝对路径。

### 批量异步提交

```bash
curl -X POST http://127.0.0.1:18000/api/v1/transcriptions/local/batch \
  -H 'Content-Type: application/json' \
  -d '{
    "paths": [
      "/absolute/path/to/a.mp4",
      "/absolute/path/to/b.mp4"
    ],
    "format": "md"
  }'
```

也可以直接使用脚本：

```bash
PYTHONPATH=src python scripts/local_batch_submit.py --paths-file /absolute/path/to/files.txt
```

如果需要边跑边查：

```bash
PYTHONPATH=src python scripts/local_batch_submit.py \
  --paths-file /absolute/path/to/files.txt \
  --poll --interval 30
```

## 运维脚本

导出批次报告：

```bash
PYTHONPATH=src python scripts/job_admin.py report --batch-id <batch_id>
```

清理历史 job：

```bash
PYTHONPATH=src python scripts/job_admin.py cleanup --older-than-hours 24
PYTHONPATH=src python scripts/job_admin.py cleanup --older-than-hours 24 --apply
```

重试批次中的失败任务：

```bash
PYTHONPATH=src python scripts/job_admin.py retry-failed --batch-id <batch_id>
```
