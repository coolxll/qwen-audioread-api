# qwen2api 优化计划

最后更新：2026-04-10

## 目标

把 `qwen2api` 收口成一个**独立发布项目**：

- 手册不再以“迁移层 / 外部依赖仓库”视角描述项目
- 默认配置以当前仓库自身为准
- 运行所需的内置执行链路、依赖、示例配置、运维说明都在本仓库内闭环

## 当前状态

已完成但尚未最终验收的改动：

- `README.md`
  - 已改成独立项目表述
  - 已移除固定外部仓库路径写法
  - 已补充 `.auth/`、`accounts.json`、`accounts.example.json` 说明
- `docs/API.md`
  - 已保留接口说明，收口为服务自身文档
- `docs/OPERATIONS.md`
  - 已改成相对路径与仓库内运维说明
- `.env.example`
  - 已改为相对路径默认值
  - 已说明 `QWEN2API_QWEN_ROOT` 仅作为可选覆盖项
- `.gitignore`
  - 已忽略 `.auth/` 与 `accounts.json`
- `accounts.example.json`
  - 已新增多账号示例文件
- `scripts/login_qwen.py`
  - 已新增首次登录脚本，用于人工登录后保存 Playwright storage state

当前还没落完的关键点：

1. 收尾检查
   - 测试已跑通
   - 文档已做过一轮人工收口
   - 仍可在最终提交前再做一次整体 review

## 下一步执行顺序

### P0：已完成

涉及文件：`src/qwen2api/config.py`

已完成事项：

- `QWEN2API_QWEN_ROOT` 默认值已改成当前项目根目录
- `QWEN2API_QWEN_DOTENV` 默认值已改成 `.env`
- `QWEN2API_QWEN_AUTH_STATE` 默认值已改成 `.auth/qwen-storage-state.json`
- `QWEN2API_QWEN_ACCOUNTS_FILE` 默认值已改成 `accounts.json`
- 相对路径会按仓库根目录解析，不再受当前 shell cwd 影响

验收标准：

- 默认配置不再依赖外部绝对路径
- 手册描述与代码默认值一致

### P1：已完成

涉及文件：`pyproject.toml`

已完成事项：

- 已增加 `playwright` 依赖
- 如后续确认还有运行时硬依赖，再一起补充

验收标准：

- README 里的安装步骤与 `pyproject.toml` 对齐

### P2：已完成

涉及路径：`src/qwen_web_capture/`

已完成事项：

- 已确认该目录作为项目内置执行链路保留
- 已纳入 git 跟踪
- 后续 review 将能把它视为正式 patch 的一部分

验收标准：

- `git diff main` 能看到该目录，而不再只是 untracked

### P3：已完成

建议先做：

- 已增加一个配置默认值测试，确保默认路径是仓库内路径
- 现有测试集已通过：`12 tests / OK`
- 已通读并统一三份文档：`README.md`、`docs/API.md`、`docs/OPERATIONS.md`

验收标准：

- 默认配置行为可测试
- 文档之间不互相矛盾

## 建议执行命令

### 查看当前改动

```bash
git status --short
git diff --stat
```

### 后续完成配置改动后建议执行

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

### 如需确认文档是否仍包含旧迁移表述

```bash
rg -n "openclaw-qwen-web-capture-skill|/Users/gq/Projects|底层项目|迁移层" README.md docs .env.example src/qwen2api
```

## 交接备注

如果下次对话中断，优先按下面顺序恢复：

1. 先看本文件 `docs/OPTIMIZATION_PLAN.md`
2. 再看 `git status --short`
3. 然后优先完成 `P0 -> P1 -> P2 -> P3`

不要直接扩散到新需求，先把“独立项目收口”做完整。
