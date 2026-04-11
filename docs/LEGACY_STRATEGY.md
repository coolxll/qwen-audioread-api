# Legacy Backend 策略文档

## 决策总结

**Playwright backend 保留为可选回退路径，但不再是默认实现。**

---

## 背景

本项目经历了从 Playwright 浏览器自动化到纯 HTTP runtime 的迁移。迁移完成后，面临一个架构决策：

> 旧的 `qwen_web_capture/` 模块应该如何处理？

### 可选方案

#### 方案 A：立即删除 ❌
- **优点**：代码库更干净，减少维护负担
- **缺点**：
  - 失去安全网，HTTP runtime 出问题时无法回退
  - 部分用户可能仍依赖 Playwright 路径（例如需要完整浏览器交互的场景）
  - 无法进行 A/B 对照验证

#### 方案 B：保留为 fallback ✅（当前决策）
- **优点**：
  - 提供安全网，HTTP runtime 异常时可临时切换
  - 支持特殊场景（例如未来远端接口变更，HTTP 链路暂时不可用）
  - 可作为对照实现，用于验证 HTTP runtime 的正确性
  - 给用户充足时间迁移到新的认证格式
- **缺点**：
  - 代码库保留了一些不再默认使用的代码
  - 需要维护两套依赖（但 Playwright 已是 optional）

#### 方案 C：完全平等双后端 ❌
- **优点**：用户可自由选择
- **缺点**：
  - 增加维护复杂度
  - 两个后端需要保持功能对等
  - 分散开发精力

---

## 当前策略

### 1. 默认行为

```bash
# 默认使用 HTTP runtime
QWEN2API_RUNTIME=http  # 或不设置（默认值就是 http）
```

### 2. 回退路径

```bash
# 需要时可切换回 Playwright
QWEN2API_RUNTIME=playwright
```

### 3. 代码定位

| 模块 | 定位 | 维护级别 |
|------|------|----------|
| `src/qwen_http_runtime/` | **主实现** | 活跃开发 |
| `src/qwen_web_capture/` | **Legacy fallback** | 仅 bug 修复 |

### 4. 依赖处理

`pyproject.toml` 中：
- `playwright` 保留在依赖中
- 但不再作为核心运行时依赖（HTTP runtime 不需要）
- 用户可选择不安装 Playwright（仅使用 HTTP runtime）

---

## 何时可以删除 Legacy？

满足以下**所有条件**时，可以考虑彻底删除 `qwen_web_capture/`：

1. ✅ HTTP runtime 稳定运行至少 3 个月
2. ✅ 无重大线上事故归因于 HTTP runtime
3. ✅ 所有用户已迁移到新的轻量认证格式
4. ✅ 远端千问接口未发生破坏性变更
5. ✅ 有明确的回滚方案（例如保留 Git 历史）

**当前状态**：正在进行中，尚未满足条件 1。

---

## 维护原则

### 对 `qwen_web_capture/` 的修改原则

1. **不主动增强**：不再为该模块添加新功能
2. **仅修复 Bug**：只修复影响 fallback 可用性的问题
3. **不破坏兼容性**：保持现有接口不变，确保需要时可切换
4. **标记废弃**：在模块文档和注释中标记为 legacy

### 对 `qwen_http_runtime/` 的开发原则

1. **默认优化**：所有新功能和优化优先应用于 HTTP runtime
2. **独立演进**：不要求与 Playwright backend 保持功能对等
3. **充分测试**：新增功能必须有对应的测试覆盖

---

## 文档更新建议

### README.md

在快速开始部分添加说明：

```markdown
## 运行时说明

当前项目默认使用 HTTP runtime（纯 HTTP 客户端），不再依赖 Playwright。

旧的 Playwright backend 仍保留在 `src/qwen_web_capture/` 中，作为回退路径。
如需切换，设置环境变量：

```bash
QWEN2API_RUNTIME=playwright
```

建议优先使用 HTTP runtime，除非有特殊需求。
```

### 迁移指南

为使用旧版本的用户提供迁移路径：

```markdown
## 从旧版本迁移

如果你之前使用的是 Playwright backend：

1. 默认行为已变更：现在使用 HTTP runtime
2. 认证文件格式：支持新的轻量格式（推荐）和旧格式（兼容）
   - 新格式：`{"tongyi_sso_ticket": "your-cookie"}`
   - 旧格式：完整 Playwright storage-state JSON
3. 如需回退：设置 `QWEN2API_RUNTIME=playwright`
4. 转换工具：使用 `scripts/convert_to_minimal_auth.py` 转换旧认证文件
```

---

## 沟通计划

### 对内（开发团队）

- [x] 本文档明确 legacy 策略
- [x] HTTP_RUNTIME_EXPERIMENT.md 已更新状态
- [ ] 在 PR 描述中说明变更影响

### 对外（用户）

- [ ] README.md 更新运行时说明
- [ ] 发布 release notes（如果发版）
- [ ] 更新 QUICKSTART.md 反映新的默认行为

---

## 相关决策记录

### 2026-04-11：决定保留 Playwright backend

**决策**：保留为 fallback，不立即删除

**原因**：
1. HTTP runtime 刚刚成为默认，需要观察期
2. 提供安全网，降低迁移风险
3. 给用户充足时间适应新格式
4. 保留对照验证能力

**评审人**：待确认

**复审日期**：2026-07-11（3 个月后）
