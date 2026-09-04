[English](README.md) | 中文版

---

# Auto-zcode-research-in-sleep（ARIS ZCode 移植版）

> 本仓库是 [Auto-claude-code-research-in-sleep (ARIS)](./Auto-claude-code-research-in-sleep) 在 ZCode 上的移植封装（wrapper）。不改上游本身，以硬拷贝方式把能力分发到论文项目，允许按项目独立定制。

上游：`Auto-claude-code-research-in-sleep` — 86 个可组合 Markdown Skill 驱动的科研全生命周期框架（idea → 实验 → 写作 → 审稿 → rebuttal），跨模型对抗协作（Executor 写、Reviewer 审）。

---

## 1. 与上游的关系

- **vendor  pristine**：`Auto-claude-code-research-in-sleep/` 作为子目录原样保留（带完整 `.git`），不在此目录内做任何修改。
- **论文项目硬拷贝**：通过根目录 `init.py` 将需要的部分**硬拷贝**到目标论文目录，而非软链。好处：论文项目获得`SKILL.md` 等文件的独立实体，可按需改提示词、改流程而不污染上游、也不影响其他论文项目。
- **白名单分发**：`init.py` 维护白名单（主要为 `skills/`、`tools/`、`.zcode/agents/`、模板与文档），更新时重新执行即可与上游对齐。

目录（wrapper 根）：

```
Auto-zcode-research-in-sleep/
├─ Auto-claude-code-research-in-sleep/  # 上游 vendor，原样保留
├─ init.py                               # 唯一分发脚本（白名单硬拷贝，默认硬拷贝，--link 可切软链）
├─ .zcode/agents/gpt-worker.md          # 单一通用审稿代理（见下，名字即家族声明）
├─ README.md                             # 本文件
└─ .git / .gitattributes
```

---

## 2. 快速开始

在 wrapper 根执行，将能力安装到任意论文项目：

```bash
# 在任意空目录初始化一篇论文
python init.py D:/AIWorkSpace/my-paper --all --quiet
# 已有项目增量对齐上游
python init.py D:/AIWorkSpace/my-paper --reconcile
# 预览不落地
python init.py D:/AIWorkSpace/my-paper --all --dry-run
# 如需软链投影而非硬拷贝（不推荐常规使用）
python init.py D:/AIWorkSpace/my-paper --all --link
```

安装后在论文项目内会得到：

- `.zcode/skills/<skill-name>/SKILL.md` — 每个 skill 的独立硬拷贝（论文项目可直接改）
- `.aris/tools/` — `tools/` 的硬拷贝，供 SKILL 内 Canonical Helper 解析链使用
- `.aris/installed-skills.txt` — 清单，记录 `repo_root`（vendor 绝对路径）、已安装/已拒绝的 skill，便于 `$ARIS_REPO` 回源与增量对齐
- `~/.aris/repo` — 全局指针，指向 wrapper 内的 vendor 路径
- `.zcode/agents/gpt-worker.md` — 单一通用审稿代理（名字即家族声明）
- `AGENTS.md` — 仅追加/更新托管块 `<!-- ARIS:BEGIN --> ... <!-- ARIS:END -->`，不 덮写用户已有内容（compare-and-swap）

常用选项（透传上游选择逻辑）：`--groups A,B` 按组安装、`--skills X,Y` 追加、`--exclude X,Y` 拒绝、`--all` 全量、`--add-new`/`--skip-new` 对新增上游 skill 的取舍、`--no-hooks`/`--no-doc` 跳过对应步骤、`--uninstall` 仅卸载清单内条目。

---

## 3. 关键设计决策

### 3.1 为什么默认硬拷贝

上游设计假设 skill 可按需演进。本移植刻意让论文项目**拥有实体文件**：改一篇文章的审稿提示词、实验流程，不会串改其他文章，也不会污染 vendor。上游更新时以 `--reconcile` 重新对齐，冲突需显式解决。

### 3.2 单一通用审稿代理 `gpt-worker`

` .zcode/agents/gpt-worker.md` 为极简通用代理：

```yaml
---
name: gpt-worker
description: "GPT-family worker. Delegates to task prompt for role and checklist. Reads files at given paths."
tools: Read, Glob, Grep, Bash, WebFetch, WebSearch
---
Follow the task prompt in each call exactly. Read the files at the given paths yourself.
```

- 不绑定模型、不固化身份，**角色由每次 Task 的 prompt 决定**（proof-checker / research-review 等提示词在 skill 侧）。
- 名字即家族声明：`gpt-worker` 表明默认 GPT 家族，与执行者（Claude）异源，满足上游同源检查（`provenance.py model_family()` 按 `gpt` 关键字归入 openai 家族）。换家族请同步改名（如 `deepseek-reviewer`）。
- 用户如需指定审稿模型，自行在该文件追加 `model: ...` 一行即可；不写则沿用 ZCode 默认。
- 29 个会调外部审稿的 skill（机器扫描 `mcp__codex__codex:` 调用块得出的全集，另加编排型 `experiment-audit`）在硬拷贝后会**幂等追加**分支：

  ```md
  <!-- ZCODE:GPT-WORKER -->
  **If `Task(agent_type: gpt-worker)` is available (ZCode default):**
    Use `Task(agent_type: gpt-worker, prompt: "<same prompt that would go to codex>")`.
    No model or reasoning parameters needed per call — the reviewer model is treated as sharing the agent's name.
    Hard `mcp__codex__codex` call remains as fallback when `gpt-worker` is unavailable.
  ```

  优先走 `gpt-worker`，不可用时回退到 `mcp__codex__codex`。vendor 文件永不被改。旧版 `aris-agent` / `ZCODE:ARIS-AGENT / ZCODE:GPT-REVIEWER (旧版，自动迁移)` 标记在重装时自动原位迁移，不重复追加。

### 3.3 路径双轨：`.zcode` 与 `.aris` 分工

- **`.zcode/skills`、`.zcode/agents`** — 走 ZCode 发现与优先级（`~/.zcode/skills` > `~/.agents/skills` > `<repo>/.zcode/skills` > 插件，路径即身份，同名高优者遮蔽）。
- **`.aris/tools`、`.aris/installed-skills.txt`、`~/.aris/repo`** — 保持 ARIS 原生路径，使 `SKILL.md` 内 Canonical Helper 解析链无需改动：

  ```bash
  # SKILL 内解析（节选）
  HELPER=".aris/tools/<helper>"; [ -f "$HELPER" ] || HELPER="tools/<helper>"
  [ -f "$HELPER" ] || { [ -n "${ARIS_REPO:-}" ] && HELPER="$ARIS_REPO/tools/<helper>"; }
  # ARIS_REPO 来自 .aris/installed-skills.txt 的 repo_root 或 ~/.aris/repo
  ```

  模板按第 3 层 `$ARIS_REPO/templates/XXX` 回源读取，不在论文项目预置副本。

### 3.4 AGENTS.md 托管块

`init.py` 仅维护 `<!-- ARIS:BEGIN --> ... <!-- ARIS:END -->` 块，不生成 `# Project: <dirname>` 之类标题，不追加 Pipeline Status，避免画蛇添足。用户可在块前后自由补充自有规范。

---

## 4. 迁移状态

| 能力 | 状态 | 说明 |
|------|------|------|
| Skills（86 个）硬拷贝到 `.zcode/skills` | ✅ 已完成 | 白名单分发，支持 `--groups`/`--skills`/`--exclude` 筛选，幂等 |
| Tools 硬拷贝到 `.aris/tools` | ✅ 已完成 | 含 `meta_opt/`、`corpus_write_guard.py` 等；支持 `--link` 切软链 |
| Agents（`gpt-worker`） | ✅ 已完成 | 单一通用代理，名字即家族声明，prompt 决定角色 |
| AGENTS.md 托管块 | ✅ 已完成 | compare-and-swap，仅维护托管块 |
| Skill 审稿分支补丁（29 个） | ✅ 已完成 | 追加 `gpt-worker` 分支，`mcp__codex__codex` 保留回退 |
| Manifest / 全局指针 | ✅ 已完成 | `.aris/installed-skills.txt` + `~/.aris/repo` 支撑增量对齐与 `$ARIS_REPO` 解析 |
| **Hooks** | **🚧 迁移未完成** | **见下节** |
| MCP Servers（codex / llm-chat / gemini-review 等） | ⏳ 待验证 | ZCode 工作区 MCP 已改为自动连接，但本移植尚未在 ZCode 会话中端到端验证 |
| Plugins（工作区 scope） | ⚠️ 受限 | ZCode 工作区不支持插件，仅全局插件生效；ARIS 插件式 hooks 需改走配置式 hooks |

### Hooks — 迁移未完成（当前不影响主链路）

上游通过 `templates/claude-hooks/meta_logging.json`（`PostToolUse`、`PostToolUseFailure`、`UserPromptSubmit`、`SessionStart`、`SessionEnd`）与 `corpus_write_guard.json`（`PreToolUse: Bash`）注册 hooks，脚本位于 `tools/meta_opt/log_event.sh`、`check_ready.sh`、`corpus_write_guard.py`。

ZCode 的差异导致需适配：

- 配置式 hooks 默认**不启用**，必须 `hooks.enabled: true`（见 `zcode-configuration-guide` / `diagnosing-hooks`）。
- 仅支持 7 个事件：`SessionStart`、`UserPromptSubmit`、`PreToolUse`、`PermissionRequest`、`PostToolUse`、`PostToolUseFailure`、`Stop`。上游 `SessionEnd` 需映射为 `Stop`。
- 模板变量 `CLAUDE_PROJECT_DIR` 需替换为 `ZCODE_PROJECT_DIR`；`async` 字段当前无运行时效果。
- 图形界面对 `.zcode/config.json` 的整文件写入会覆盖文件式 hooks（实测：界面新增两条测试 hooks 后原有 6 条事件被覆盖为 2 条），需做按 `(matcher, command)` 去重的合并策略。
- 校验路径为文件内容与 `.aris/meta/events.jsonl` 事件日志，而非界面“已安装 N”计数（后者仅统计界面新增的交互式 hooks）。

`init.py:ensure_hooks()` 已实现雏形（映射 `CLAUDE_PROJECT_DIR→ZCODE_PROJECT_DIR`、`SessionEnd→Stop`、写入 `.zcode/config.json` 的 `hooks.enabled/events`，对已存在事件做增量合并），但尚未通过 ZCode 真实会话的生命周期触发与日志落盘完成联调，故本版 README 将其标为**迁移未完成**。不影响 skills 主链路执行；需要审计/回溯时再补齐联调。

---

## 5. 上游更新

```bash
# 1) 更新 vendor
cd Auto-claude-code-research-in-sleep && git pull

# 2) 让所有论文项目重新对齐（在 wrapper 根执行）
python init.py D:/AIWorkSpace/my-paper --reconcile        # 交互式处理新增
python init.py D:/AIWorkSpace/my-paper --reconcile --add-new   # 自动接受新增
python init.py D:/AIWorkSpace/my-paper --reconcile --skip-new  # 自动跳过新增
```

如在论文项目内单独改过某个 `SKILL.md`，对齐时该文件不会被静默覆盖，需显式处理冲突。

---

## 6. 注意事项

- 不要直接修改 `Auto-claude-code-research-in-sleep/skills/` 下的文件；应在派生的论文项目内修改 `.zcode/skills/<name>/SKILL.md`。
- ZCode 配置优先级：用户 scope（`~/.zcode/skills`）> 工作区（`<repo>/.zcode/skills`）> 插件。同名 skill 仅首个生效，注意遮蔽。
- 模板与共享引用不拷贝，按 Canonical Helper 第 3 层经 `$ARIS_REPO` 回源；论文项目内无需也不应预置 `templates/`。
- 审稿链路当前默认走 `gpt-worker`，如环境未提供该 agent 则回退到 `mcp__codex__codex`；如需走 `llm-chat` 通用网关或 `manual-review`，按对应 skill 的 `REVIEWER_BACKEND` 选项配置。
- Hooks 未完成期间，`meta-optimize` 的被动日志与 `corpus_write_guard` 的 Bash 写拦截不生效；主流程可用，审计能力待补。

---

## 7. 参考

- 上游文档：`Auto-claude-code-research-in-sleep/README.md`、`AGENT_GUIDE.md`、`docs/ARIS_INTRO.md`
- ZCode 配置：`zcode-configuration-guide`（5 类资源、发现顺序、合并规则）、`diagnosing-hooks`（hooks 排障）
- 关键脚本：`init.py`（分发与对齐）、`Auto-claude-code-research-in-sleep/tools/install_aris.sh`（上游原版安装逻辑对照）
