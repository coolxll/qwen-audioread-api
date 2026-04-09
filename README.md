# qwen2api

一个面向本地/私有部署的原生 API 服务，用来把通义千问「音视频速读」网页链路包装成可调用的 HTTP 接口。

当前阶段是原生 API MVP，优先提供真实能力，不先做 OpenAI 兼容层。

## 当前接口

- `GET /health`
- `POST /api/v1/transcriptions`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/file`

## 当前能力

- 接收单个音频/视频文件上传
- 调用 `qwen_web_capture` 真实链路完成上传、转写、导出
- 默认支持导出 `md`，也支持 `docx`
- 本地保存 job 元数据、输入文件、输出文件、错误日志
- 支持指定账号和账号策略
- 可选 API Key 校验

## 目录说明

```text
/Users/gq/Projects/qwen2api
├── src/qwen2api/
├── data/
│   ├── jobs/
│   └── runtime/
├── pyproject.toml
├── .env.example
└── README.md
```

说明：

- `data/jobs/<job_id>/input.*`：上传的原始文件
- `data/jobs/<job_id>/outputs/*`：导出结果
- `data/jobs/<job_id>/job.json`：任务状态与结果
- `data/jobs/<job_id>/error.txt`：失败时的错误摘要
- `data/runtime/`：账号轮询状态和 quota 状态

## 环境准备

先确认底层项目已经可用：

- `/Users/gq/Projects/openclaw-qwen-web-capture-skill`
- 其中登录态、`.env`、Playwright 依赖都已准备好

建议先复制配置：

```bash
cd /Users/gq/Projects/qwen2api
cp .env.example .env
```

关键配置项：

- `QWEN2API_QWEN_ROOT`：底层 Python 项目根目录
- `QWEN2API_QWEN_DOTENV`：底层 `.env` 路径
- `QWEN2API_QWEN_AUTH_STATE`：默认登录态文件
- `QWEN2API_QWEN_ACCOUNTS_FILE`：账号池配置文件
- `QWEN2API_DEFAULT_FORMAT`：默认导出格式
- `QWEN2API_DELETE_REMOTE`：是否默认删除远端记录
- `QWEN2API_API_KEY`：可选 API Key

## 启动服务

开发模式：

```bash
cd /Users/gq/Projects/qwen2api
PYTHONPATH=src uvicorn qwen2api.main:app --host 0.0.0.0 --port 8000 --reload
```

如果你设置了 `.env`，代码会自动读取 `/Users/gq/Projects/qwen2api/.env`。

## 接口示例

### 1. 健康检查

```bash
curl http://127.0.0.1:8000/health
```

返回示例：

```json
{"status":"ok"}
```

### 2. 创建转写任务

```bash
curl -X POST http://127.0.0.1:8000/api/v1/transcriptions   -F 'file=@/absolute/path/to/demo.mp4'   -F 'format=md'   -F 'delete_remote=true'
```

可选字段：

- `format`：`md` / `markdown` / `docx`
- `delete_remote`：`true` / `false`
- `account`：指定账号 id
- `account_strategy`：`round-robin` / `failover` / `sticky`

返回示例：

```json
{
  "job_id": "job_20260409T132000Z_ab12cd34",
  "status": "succeeded",
  "format": "md",
  "content_type": "text/markdown",
  "text": "...markdown内容...",
  "output_file": "/Users/gq/Projects/qwen2api/data/jobs/job_xxx/outputs/demo-2026-04-09T13-20-00+00-00.md",
  "download_url": "/api/v1/jobs/job_20260409T132000Z_ab12cd34/file",
  "record_id": "...",
  "gen_record_id": "...",
  "remote_deleted": true,
  "account_id": "account-a",
  "account_label": "账号A",
  "original_filename": "demo.mp4",
  "created_at": "2026-04-09T13:20:00+00:00",
  "updated_at": "2026-04-09T13:21:05+00:00",
  "completed_at": "2026-04-09T13:21:05+00:00",
  "error": null,
  "meta": {
    "delete_remote": true,
    "account_strategy": "round-robin",
    "input_file": "/Users/gq/Projects/qwen2api/data/jobs/job_xxx/input.mp4",
    "job_dir": "/Users/gq/Projects/qwen2api/data/jobs/job_xxx",
    "output_suffix": ".md"
  }
}
```

### 3. 查询任务

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
```

### 4. 下载导出文件

```bash
curl -L http://127.0.0.1:8000/api/v1/jobs/<job_id>/file -o result.md
```

## API Key

如果设置了 `QWEN2API_API_KEY`，请求时需要携带任一头：

```bash
-H 'Authorization: Bearer your-key'
```

或：

```bash
-H 'X-API-Key: your-key'
```

## 当前限制

这还是 MVP，暂时有这些限制：

- 仅支持单文件同步转写
- 暂不提供批量接口
- 暂不提供异步队列接口
- 暂不做 OpenAI 兼容层
- 错误映射还是第一版，后续可以继续细化
- `text` 字段当前直接返回导出文件全文；如果是 `docx`，则不会内联正文

## 下一步建议

等这版原生 API 跑顺后，再做：

1. 更细的错误映射
2. job 列表接口
3. 异步任务模式
4. OpenAI 兼容层 `/v1/audio/transcriptions`
