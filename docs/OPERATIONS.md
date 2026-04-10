# qwen2api 运维文档

## 1. 启动服务

```bash
cd /Users/gq/Projects/qwen2api
PYTHONPATH=src uvicorn qwen2api.main:app --host 0.0.0.0 --port 18000 --reload
```

---

## 2. 目录说明

- `data/outputs/`
  - 最终发布用 Markdown 成品
- `data/jobs/<job_id>/`
  - 单任务工作目录
- `data/runtime/`
  - 批次视图、运行时状态、报告目录
- `data/runtime/reports/`
  - 通过脚本或接口导出的批次报告

---

## 3. 推荐调用方式

### 本机已有视频文件

优先使用：

- `POST /api/v1/transcriptions/local/async`
- `POST /api/v1/transcriptions/local/batch`

理由：

- 不走 HTTP 大文件上传
- 创建任务更快返回
- 更适合大体积批量视频

### 远端客户端上传文件

使用：

- `POST /api/v1/transcriptions/async`
- `POST /api/v1/transcriptions/batch`

---

## 4. 默认清理策略

当前默认行为：

- 上传模式产生的临时输入副本：成功后删除
- job 目录中的中间输出：成功后删除
- `job.json`：默认不保留正文全文
- 最终成品：始终保留在 `data/outputs/`

相关配置：

```bash
QWEN2API_KEEP_JOB_TEXT=false
QWEN2API_KEEP_UPLOADED_INPUT=false
QWEN2API_KEEP_INTERMEDIATE_OUTPUTS=false
```

---

## 5. 队列与恢复

当前为进程内持久队列模型：

- `queued` 任务会被 worker 消费
- 服务重启后：
  - `queued` 任务会重新入队
  - `running` 任务会被标记回 `queued` 并重新执行

相关配置：

```bash
QWEN2API_JOB_WORKERS=2
```

---

## 6. 自动重试

默认会对临时性失败做自动重试。

当前默认配置：

```bash
QWEN2API_MAX_RETRIES=2
QWEN2API_RETRY_DELAY_SECONDS=30
QWEN2API_RETRYABLE_ERROR_CODES=TRANSCRIPTION_TIMEOUT,RATE_LIMITED,TRANSCRIPTION_FAILED
```

说明：

- `retry_count` 会记录在 job 元数据里
- 达到最大重试次数后不再自动重试

---

## 7. 批次报告

### 接口方式

```bash
curl 'http://127.0.0.1:18000/api/v1/batches/<batch_id>/report?format=md'
curl 'http://127.0.0.1:18000/api/v1/batches/<batch_id>/report?format=json'
```

### 脚本方式

```bash
cd /Users/gq/Projects/qwen2api
PYTHONPATH=src python scripts/job_admin.py report --batch-id <batch_id>
```

生成位置：

- `data/runtime/reports/<batch_id>.md`
- 或 `json`

---

## 8. 清理历史任务

默认 dry-run：

```bash
PYTHONPATH=src python scripts/job_admin.py cleanup --older-than-hours 24
```

实际执行：

```bash
PYTHONPATH=src python scripts/job_admin.py cleanup --older-than-hours 24 --apply
```

建议：

- 先 dry-run
- 再执行 `--apply`

---

## 9. 失败任务重试

### 查看失败候选

```bash
curl 'http://127.0.0.1:18000/api/v1/batches/<batch_id>/retry-candidates'
```

### 脚本触发重试

```bash
PYTHONPATH=src python scripts/job_admin.py retry-failed --batch-id <batch_id>
```

---

## 10. 常见检查项

### 检查服务是否存活

```bash
curl http://127.0.0.1:18000/health
```

### 检查批次状态

```bash
curl http://127.0.0.1:18000/api/v1/batches/<batch_id>
```

### 检查单任务状态

```bash
curl http://127.0.0.1:18000/api/v1/jobs/<job_id>
```

### 下载成品

```bash
curl -L http://127.0.0.1:18000/api/v1/jobs/<job_id>/file -o result.md
```
