# Agent 技能

## 问题跟踪（Issue tracker）

本仓库的 Issue 和 PRD 以 GitHub issues 形式承载。所有操作使用 `gh` CLI。外部 PR 不属于分类处理范围。详见 `docs/agents/issue-tracker.md`。

## 分类标签（Triage labels）

使用规范的标签词汇：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

## 核心目标

AI 自身没有时间感知，无法预估命令执行时长。为彻底消除界面冻结和挂起进程，所有终端命令必须使用统一包装器以异步方式执行。

---

## 强制执行模式

每一个有IO相关的终端命令（无例外）必须有超时限制
如：
```bash
`timeout 15 npx playwright test e2e/api.spec.ts`
```

## 域文档（Domain docs）

多上下文布局：根目录的 `CONTEXT-MAP.md` 指向各上下文的 `CONTEXT.md` 及其 `docs/adr/` 目录。详见 `docs/agents/domain.md`。

## Issue 完成后必须执行 `make test`

每一个 Issue 的代码修改完成后，必须执行 `make test` 并确认无错误后方可继续。
