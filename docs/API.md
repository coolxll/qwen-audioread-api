# qwen2api API 文档

## 基础说明

- Base URL：`http://127.0.0.1:18000`
- 输出格式：仅支持 `md`
- 鉴权：如配置 `QWEN2API_API_KEY`，可使用：
  - `Authorization: Bearer <key>`
  - `X-API-Key: <key>`

---

## 1. 健康检查

### `GET /health`

返回：

```json
{"status":"ok"}
```

---

## 2. 上传模式

### `POST /api/v1/transcriptions`

同步转写。

表单字段：

- `file`
- `format=md`
- `delete_remote` 可选
- `account` 可选
- `account_strategy` 可选

### `POST /api/v1/transcriptions/async`

异步转写。  
注意：上传模式下，HTTP 大文件上传仍然要先完成，接口才会返回 `202`。

返回关键字段：

- `job_id`
- `status=queued`
- `markdown_filename`
- `suggested_poll_after_seconds`

### `POST /api/v1/transcriptions/batch`

上传模式批量异步提交。

返回关键字段：

- `batch_id`
- `output_dir`
- `items[].job_id`
- `items[].markdown_filename`
- `items[].suggested_poll_after_seconds`

---

## 3. 本地路径模式

适合“文件已在本机磁盘上”的场景，不需要再做 HTTP 大文件上传。

### `POST /api/v1/transcriptions/local`

请求体：

```json
{
  "path": "/absolute/path/to/demo.mp4",
  "format": "md",
  "delete_remote": true,
  "account": "",
  "account_strategy": "round-robin"
}
```

### `POST /api/v1/transcriptions/local/async`

本地路径异步提交。  
这类接口会更快返回，因为不走 multipart 上传。

### `POST /api/v1/transcriptions/local/batch`

请求体：

```json
{
  "paths": [
    "/absolute/path/to/a.mp4",
    "/absolute/path/to/b.mp4"
  ],
  "format": "md"
}
```

---

## 4. 批次查询

### `GET /api/v1/batches/{batch_id}`

查询批次当前状态。

### `GET /api/v1/batches/{batch_id}/report?format=md`

导出 Markdown 报告。

### `GET /api/v1/batches/{batch_id}/report?format=json`

导出 JSON 报告。

报告包含：

- 成功率 / 失败率 / 完成率
- 批次总耗时
- 已完成任务平均耗时 / 最慢 / 最快
- 总输入体积 / 平均输入体积
- 失败原因分组
- 来源模式分组

### `GET /api/v1/batches/{batch_id}/retry-candidates`

列出该批次中当前可用于重试的失败任务候选。

---

## 5. Job 查询

### `GET /api/v1/jobs`

查询任务列表。

### `GET /api/v1/jobs/{job_id}`

查询单个任务详情。

### `GET /api/v1/jobs/{job_id}/file`

下载最终 Markdown 文件。

---

## 6. 状态定义

- `queued`：已入队，等待执行
- `running`：执行中
- `succeeded`：执行成功
- `failed`：执行失败

---

## 7. 返回中的重要字段

### `markdown_filename`

最终目标 Markdown 文件名。  
系统会尽量保留原始标题，并在重名时自动追加后缀。

### `suggested_poll_after_seconds`

建议查询间隔。当前规则：

- `<= 100MB` → `60`
- `100MB ~ 250MB` → `90`
- `> 250MB` → `120`

### `batch_id`

批量任务标识，用于后续批次查询、报告导出、失败任务筛选。

---

## 8. 常见错误码

- `MD_ONLY_OUTPUT`
- `UNAUTHORIZED`
- `JOB_NOT_FOUND`
- `BATCH_NOT_FOUND`
- `JOB_NOT_READY`
- `OUTPUT_MISSING`
- `LOCAL_FILE_NOT_FOUND`
- `LOCAL_FILE_INVALID`
- `EMPTY_PATHS`
- `UNSUPPORTED_REPORT_FORMAT`

