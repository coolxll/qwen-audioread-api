# qwen2api

一个面向本地/私有部署的原生 API 服务，用来把通义千问「音视频速读」网页链路包装成可调用的 HTTP 接口。

当前版本重点面向**发布用途**，输出被强制收敛为：

- **只产出 Markdown (`md`)**

不再支持 `docx` 导出，以避免后续发布工作流里出现多种格式分叉。

## 当前接口

- `GET /health`
- `POST /api/v1/transcriptions`
- `POST /api/v1/transcriptions/async`
- `POST /api/v1/transcriptions/batch`
- `GET /api/v1/batches/{batch_id}`
- `GET /api/v1/jobs`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/file`

## 当前能力

- 接收单个音频/视频文件上传
- 调用 `qwen_web_capture` 真实链路完成上传、转写、导出
- 导出格式固定为 `md`
- 支持同步调用、异步调用、批量异步提交
- 异步/批量返回推荐查询时间 `suggested_poll_after_seconds`
- 批量任务支持 `batch_id` 查询
- 本地保存 job 元数据、输入文件、输出文件、错误日志
- 支持指定账号和账号策略
- 可选 API Key 校验

## 输出约束

这版开始，服务的输出约束如下：

- 接口层只接受 `md` / `markdown`
- 如果传 `docx` 等其他格式，会直接返回 `400`
- 下载接口只返回 markdown 文件
- 返回体中的 `format` 永远是 `md`
- 成品统一平铺输出到 `data/outputs/`
- 成品命名固定为 `原视频文件名.md`
- 同名文件自动追加后缀，如 `课程-2.md`

这能保证后续发布链路只处理一类文件。

## 目录说明

```text
/Users/gq/Projects/qwen2api
├── src/qwen2api/
├── data/
│   ├── jobs/
│   ├── outputs/
│   └── runtime/
├── pyproject.toml
├── .env.example
└── README.md
```

说明：

- `data/jobs/<job_id>/<原始文件名>`：上传的原始文件
- `data/jobs/<job_id>/outputs/*.md`：底层导出过程中的中间产物
- `data/outputs/<原视频文件名>.md`：最终发布用 markdown 成品
- `data/jobs/<job_id>/job.json`：任务状态与结果
- `data/jobs/<job_id>/error.txt`：失败时的错误摘要
- `data/runtime/<batch_id>.json`：批次提交和查询视图
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
- `QWEN2API_DELETE_REMOTE`：是否默认删除远端记录
- `QWEN2API_API_KEY`：可选 API Key

## 启动服务

建议显式指定一个未被占用的端口，比如 `18000`：

```bash
cd /Users/gq/Projects/qwen2api
PYTHONPATH=src uvicorn qwen2api.main:app --host 0.0.0.0 --port 18000 --reload
```

## 接口示例

### 1）健康检查

```bash
curl http://127.0.0.1:18000/health
```

### 2）同步转写

```bash
curl -X POST http://127.0.0.1:18000/api/v1/transcriptions \
  -F 'file=@/absolute/path/to/demo.mp4' \
  -F 'format=md'
```

返回会阻塞到转写完成，适合小批量调用。

### 3）异步转写

```bash
curl -X POST http://127.0.0.1:18000/api/v1/transcriptions/async \
  -F 'file=@/absolute/path/to/demo.mp4' \
  -F 'format=md'
```

返回 `202`，然后用 job 接口轮询。

返回体会包含：

- `markdown_filename`
- `suggested_poll_after_seconds`

### 4）批量异步提交

```bash
curl -X POST http://127.0.0.1:18000/api/v1/transcriptions/batch \
  -F 'files=@/absolute/path/to/a.mp4' \
  -F 'files=@/absolute/path/to/b.mp4' \
  -F 'format=md'
```

返回体会包含：

- `batch_id`
- `output_dir`
- `items[].markdown_filename`
- `items[].suggested_poll_after_seconds`

### 5）查询批次

```bash
curl http://127.0.0.1:18000/api/v1/batches/<batch_id>
```

### 6）查询全部任务

```bash
curl http://127.0.0.1:18000/api/v1/jobs
```

### 7）查询单个任务

```bash
curl http://127.0.0.1:18000/api/v1/jobs/<job_id>
```

### 8）下载 markdown

```bash
curl -L http://127.0.0.1:18000/api/v1/jobs/<job_id>/file -o result.md
```

## 推荐查询时间

异步与批量接口会直接返回 `suggested_poll_after_seconds`，当前采用轻量规则：

- `<= 100MB`：60 秒
- `100MB ~ 250MB`：90 秒
- `> 250MB`：120 秒

## API Key

如果设置了 `QWEN2API_API_KEY`，请求时需要携带任一头：

```bash
-H 'Authorization: Bearer your-key'
```

或：

```bash
-H 'X-API-Key: your-key'
```

## 状态说明

任务状态当前有 4 类：

- `queued`：已创建，等待后台执行
- `running`：执行中
- `succeeded`：成功，且已产出 markdown
- `failed`：失败

## 当前限制

- 输出格式固定为 `md`
- 暂不支持取消任务
- 暂不支持去重 / 断点续跑
- 批量接口当前是“提交即排队”，不是复杂调度器
- 批次查询视图基于已提交 jobs 聚合，不做更复杂的工作流编排
- 仍然依赖底层登录态和 Qwen 网页接口可用

## 已验证情况

在真实 mp4 文件下，已经完成过：

- 8 个视频样本
- 并发数 3
- 成功 8 / 失败 0

说明当前版本已经适合继续往“发布前转写流水线”方向演进。
