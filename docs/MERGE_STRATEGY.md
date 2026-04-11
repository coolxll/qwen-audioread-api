# 主分支合并策略

## 目标

将 `dev` 分支（包含 HTTP runtime 实验）安全地合并到 `main` 分支。

---

## 变更摘要

### 统计

- **新增文件**：18 个
- **修改文件**：5 个
- **总变更**：+4092 行
- **新增测试**：42 个（原有 14 个 → 现在 56 个）

### 核心变更

| 类别 | 变更 | 影响 |
|------|------|------|
| **默认 Runtime** | `playwright` → `http` | 所有用户默认使用 HTTP backend |
| **认证格式** | 新增轻量格式支持 | 向后兼容旧格式 |
| **新模块** | `qwen_http_runtime/` | 纯 HTTP 实现，不依赖 Playwright |
| **测试覆盖** | +42 个测试用例 | service 层、auth、runtime |
| **文档** | 新增/更新 5 个文档 | 实验记录、策略、迁移指南 |

---

## 合并方案

### 推荐方案：Fast-Forward 合并 ✅

```bash
# 1. 切换到 main 分支
git checkout main

# 2. 拉取最新代码
git pull origin main

# 3. Fast-forward 合并 dev
git merge --ff-only dev

# 4. 推送
git push origin main
```

**理由**：
- dev 是从 main 创建的线性历史
- 没有并行开发冲突
- 保持干净的 Git 历史
- 可以回滚（保留所有提交记录）

### 备选方案：Squash 合并

```bash
git checkout main
git merge --squash dev
git commit -m "feat: migrate to HTTP runtime as default backend

Major changes:
- Add qwen_http_runtime module (pure HTTP implementation)
- Switch default runtime from playwright to http
- Add minimal auth file format support
- Add comprehensive test coverage (42 new tests)
- Update documentation and migration guides

See docs/HTTP_RUNTIME_EXPERIMENT.md for full experiment log.
See docs/LEGACY_STRATEGY.md for legacy backend policy."
```

**适用场景**：
- 如果希望主分支历史更简洁
- 不关心 individual commits
- 想要一个干净的"大爆炸"提交

---

## 合并前检查清单

### ✅ 代码质量

- [x] 所有测试通过（56/56）
- [x] 无编译错误
- [x] 无明显的代码异味

### ✅ 功能验证

- [x] HTTP runtime 完整链路验证
- [x] 无 Playwright 环境验证
- [x] 服务级回归验证
- [x] 默认 runtime 验证

### ✅ 文档完整性

- [x] HTTP_RUNTIME_EXPERIMENT.md 实验记录完整
- [x] LEGACY_STRATEGY.md legacy 策略明确
- [x] README.md 已更新（需确认）
- [x] QUICKSTART.md 已更新（需确认）
- [x] API.md 无变更（接口未变）

### ⚠️ 待确认

- [ ] README.md 是否需要更新运行时说明？
- [ ] QUICKSTART.md 是否需要反映新认证格式？
- [ ] 是否需要更新 .env.example 注释？

---

## 风险评估

### 低风险 ✅

| 风险 | 缓解措施 |
|------|----------|
| HTTP runtime 不稳定 | 保留 Playwright fallback，可快速切换 |
| 认证文件不兼容 | 向后兼容旧格式，提供转换工具 |
| 测试覆盖不足 | 56 个测试，覆盖核心路径 |

### 中风险 ⚠️

| 风险 | 缓解措施 |
|------|----------|
| 远端接口变更导致 HTTP 链路失效 | 保留 Playwright fallback 作为应急方案 |
| 用户不理解新认证格式 | 提供文档和转换脚本 |

### 高风险 ❌

当前无高风险项。

---

## 回滚方案

### 方案 A：Git 回滚（推荐）

```bash
# 如果合并后发现重大问题
git revert HEAD~6..HEAD  # 回滚 dev 的所有提交
```

**优点**：
- 保留合并历史
- 可以重新修复后再合并

### 方案 B：切换 Runtime（临时）

```bash
# 如果只是 HTTP runtime 问题
echo "QWEN2API_RUNTIME=playwright" >> .env
```

**优点**：
- 不需要回滚代码
- 快速应急

### 方案 C：Tag 回滚（最坏情况）

```bash
# 回到合并前的 main
git checkout v0.1.0  # 或合并前最新 tag
```

**缺点**：
- 丢失所有新功能和修复

---

## 合并后验证

### 立即验证（合并后 5 分钟）

```bash
# 1. 确认当前分支
git branch --show-current  # 应该是 main

# 2. 确认测试通过
PYTHONPATH=src python -m unittest discover -s tests -v

# 3. 确认默认 runtime 是 http
PYTHONPATH=src python -c "from qwen2api.config import get_settings; print(get_settings().runtime_backend)"
# 应该输出: http
```

### 功能验证（合并后 1 小时）

```bash
# 1. 启动服务
PYTHONPATH=src uvicorn qwen2api.main:app --host 127.0.0.1 --port 18000

# 2. 健康检查
curl http://127.0.0.1:18000/health

# 3. 测试本地路径异步转写（使用已有 auth 文件）
curl -X POST http://127.0.0.1:18000/api/v1/transcriptions/local/async \
  -H 'Content-Type: application/json' \
  -d '{"path":"/path/to/test.mp4","format":"md"}'
```

### 观察期（合并后 1 周）

- 监控错误日志
- 关注用户反馈
- 记录任何异常行为

---

## 发布计划

### 版本建议：`v0.2.0`

**语义化版本理由**：
- 新增功能（HTTP runtime）
- 默认行为变更（runtime 切换）
- 向后兼容（保留 Playwright fallback）

### Release Notes 草稿

```markdown
## v0.2.0 - HTTP Runtime Migration

### 🎉 重大变更

默认 runtime 已从 Playwright 迁移到纯 HTTP 客户端，带来以下改进：

- **更快的启动**：不再需要启动浏览器
- **更少的依赖**：无 Playwright 也能运行（默认路径）
- **更轻的认证**：支持新的轻量 auth 文件格式

### ✨ 新增功能

- 纯 HTTP runtime 后端 (`qwen_http_runtime/`)
- 轻量认证文件格式：`{"tongyi_sso_ticket": "<value>"}`
- 认证格式转换工具：`scripts/convert_to_minimal_auth.py`
- 完整的 service 层测试覆盖（+42 个测试）

### 🔄 变更

- 默认 runtime：`playwright` → `http`
- 认证文件：现在支持两种格式（向后兼容）

### ⚠️ 破坏性变更

**无**。旧的 Playwright backend 仍保留为 fallback。

### 📝 迁移指南

如果你之前使用 Playwright backend：

1. 默认行为已变更（现在使用 HTTP）
2. 如需回退：设置 `QWEN2API_RUNTIME=playwright`
3. 推荐转换认证文件：
   ```bash
   PYTHONPATH=src python scripts/convert_to_minimal_auth.py \
     --input .auth/qwen-storage-state.json \
     --out .auth/minimal.json
   ```

### 📚 文档

- [HTTP Runtime 实验记录](docs/HTTP_RUNTIME_EXPERIMENT.md)
- [Legacy Backend 策略](docs/LEGACY_STRATEGY.md)
- [分析排查手册](docs/ANALYSIS_PLAYBOOK.md)
```

---

## 执行时间线

| 阶段 | 时间 | 操作 |
|------|------|------|
| **准备** | Day 0 | 完成本文档和所有前置任务 |
| **合并** | Day 1 | 执行 Git 合并 |
| **验证** | Day 1 | 运行测试和功能验证 |
| **发布** | Day 1-2 | 打 tag、写 release notes |
| **观察** | Day 1-7 | 监控和收集反馈 |
| **复审** | Day 30 | 评估是否需要调整 |

---

## 决策记录

### 2026-04-11：选择 Fast-Forward 合并

**决策**：使用 Fast-Forward 合并到 main

**原因**：
- 保持完整的开发历史
- 每个变更都有独立的提交信息
- 便于未来排查问题

**评审人**：待确认

### 2026-04-11：版本号定为 v0.2.0

**决策**：使用 minor 版本升级

**原因**：
- 新增功能但保持向后兼容
- 默认行为变更但不是破坏性的
- 符合语义化版本规范

---

## 附录：提交列表

### dev 分支的所有提交（从新到旧）

```
0324308 docs: add legacy backend strategy document (P2)
b7fd9ea feat: add minimal auth file format support (P3)
0e33ec3 test: add service layer tests for HTTP runtime backend
ba9ea56 feat: complete http runtime module self-containment and update docs
e361d3c feat: make http runtime the default backend
5876407 feat: add http runtime experiment backend
```

### 每个提交的影响范围

| 提交 | 类型 | 影响范围 |
|------|------|----------|
| `0324308` | docs | 新增策略文档 |
| `b7fd9ea` | feat + test | 认证格式 + 转换工具 + 26 测试 |
| `0e33ec3` | test | 16 个 service 层测试 |
| `ba9ea56` | feat + docs | HTTP runtime 模块收口 + 文档 |
| `e361d3c` | feat | 默认 runtime 切换 |
| `5876407` | feat | 初始 HTTP runtime 实现 |
