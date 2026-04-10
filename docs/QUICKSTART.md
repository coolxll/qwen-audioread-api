# qwen2api 快速命令清单

这份文档是 **README 的速查版**。  
如果你是第一次接触项目，优先看：

- [README.md](../README.md)

如果你已经知道整体思路，只想快速回忆命令，就看这一页。

---

## 1. 安装依赖

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

---

## 2. 准备配置

```bash
cp .env.example .env
```

单账号默认登录态文件：

- `.auth/qwen-storage-state.json`

---

## 3. 首次登录

```bash
PYTHONPATH=src python scripts/login_qwen.py --out .auth/qwen-storage-state.json
```

多账号时分别生成：

```bash
PYTHONPATH=src python scripts/login_qwen.py --out .auth/account-1.json
PYTHONPATH=src python scripts/login_qwen.py --out .auth/account-2.json
```

---

## 4. 启动服务

```bash
PYTHONPATH=src uvicorn qwen2api.main:app --host 127.0.0.1 --port 18000
```

健康检查：

```bash
curl http://127.0.0.1:18000/health
```

---

## 5. 提交本地文件转写

```bash
curl -X POST http://127.0.0.1:18000/api/v1/transcriptions/local/async \
  -H 'Content-Type: application/json' \
  -d '{
    "path": "/absolute/path/to/demo.mp4",
    "format": "md"
  }'
```

要求：

- `path` 必须是服务所在机器可读的绝对路径

---

## 6. 查询任务状态

```bash
curl http://127.0.0.1:18000/api/v1/jobs/<job_id>
```

完成后状态应为：

- `succeeded`

---

## 7. 下载结果

```bash
curl -L http://127.0.0.1:18000/api/v1/jobs/<job_id>/file -o result.md
```

或直接查看：

- `data/outputs/`

---

## 8. 相关文档

- 新手完整说明：[`README.md`](../README.md)
- 接口细节：[`docs/API.md`](./API.md)
- 运维与排障：[`docs/OPERATIONS.md`](./OPERATIONS.md)
