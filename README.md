[中文版 README](README_ch.md) | English

---

# Auto-zcode-research-in-sleep (ARIS ZCode Port)

> This repository is a ZCode port (wrapper) of [Auto-claude-code-research-in-sleep (ARIS)](./Auto-claude-code-research-in-sleep). The upstream project is never modified; capabilities are distributed to paper projects via hard copy, so each project can be customized independently.

Upstream: `Auto-claude-code-research-in-sleep` — a full research-lifecycle framework driven by 86 composable Markdown skills (idea → experiment → writing → review → rebuttal), with cross-model adversarial collaboration (Executor writes, Reviewer reviews).

---

## 1. Relationship with Upstream

- **Pristine vendor**: `Auto-claude-code-research-in-sleep/` is kept as-is as a subdirectory (with its complete `.git`); never modify anything inside it.
- **Hard copy into paper projects**: the root-level `init.py` **hard-copies** the needed parts into the target paper directory instead of symlinking. Each paper project gets its own physical copies of files such as `SKILL.md`, so prompts and workflows can be tuned per paper without polluting upstream or affecting other paper projects.
- **Whitelist distribution**: `init.py` maintains a whitelist (mainly `skills/`, `tools/`, `.zcode/agents/`, templates and docs); re-running it re-aligns with upstream updates.

Wrapper root layout:

```
Auto-zcode-research-in-sleep/
├─ Auto-claude-code-research-in-sleep/  # upstream vendor, kept pristine
├─ init.py                               # the only distribution script (whitelist hard copy; --link switches to symlinks)
├─ .zcode/agents/gpt-reviewer.md          # single generic reviewer agent (see below; name is the family declaration)
├─ README.md                             # this file (English)
├─ README_ch.md                          # Chinese version
└─ .git / .gitattributes
```

---

## 2. Quick Start

Run from the wrapper root to install capabilities into any paper project:

```bash
# Initialize a paper in any empty directory
python init.py D:/AIWorkSpace/my-paper --all --quiet
# Incrementally re-align an existing project with upstream
python init.py D:/AIWorkSpace/my-paper --reconcile
# Preview without writing
python init.py D:/AIWorkSpace/my-paper --all --dry-run
# Project via symlinks instead of hard copy (not recommended for regular use)
python init.py D:/AIWorkSpace/my-paper --all --link
```

A paper project then contains:

- `.zcode/skills/<skill-name>/SKILL.md` — an independent hard copy of each skill (edit directly in the paper project)
- `.aris/tools/` — hard copy of `tools/`, used by the Canonical Helper resolution chain inside SKILLs
- `templates/` — hard copy of vendor `templates/` (21 files) at the project root, so bare `templates/XXX` references inside skills resolve locally without `$ARIS_REPO` back-reference; user-modified files are never overwritten
- `.aris/installed-skills.txt` — the manifest, recording `repo_root` (absolute vendor path) plus installed/declined skills, enabling `$ARIS_REPO` back-reference and incremental re-alignment
- `~/.aris/repo` — global pointer to the vendor path inside the wrapper
- `.zcode/agents/gpt-reviewer.md` — single generic reviewer agent
- `AGENTS.md` — only the managed block `<!-- ARIS:BEGIN --> ... <!-- ARIS:END -->` is appended/updated, never overwriting user content (compare-and-swap)

Common options (upstream selection logic passed through): `--groups A,B` install by group, `--skills X,Y` add extras, `--exclude X,Y` decline, `--all` everything, `--add-new`/`--skip-new` decide the fate of new upstream skills, `--no-hooks`/`--no-doc` skip those steps, `--uninstall` removes only manifest-listed entries.

---

## 3. Key Design Decisions

### 3.1 Why Hard Copy by Default

Upstream assumes skills evolve per need. This port deliberately lets each paper project **own physical files**: changing one paper's review prompt or experiment flow never leaks into other papers nor pollutes the vendor. Re-align with `--reconcile` on upstream updates; conflicts require explicit resolution.

### 3.2 Single Generic Reviewer Agent `gpt-reviewer`

`.zcode/agents/gpt-reviewer.md` is a minimal generic agent:

```yaml
---
name: gpt-reviewer
description: "GPT-family reviewer. Delegates to task prompt for role and checklist. Reads files at given paths."
tools: Read, Glob, Grep, Bash, WebFetch, WebSearch, TodoWrite
---
Follow the task prompt in each call exactly. Read the files at the given paths yourself.
```

- No pinned model, no fixed identity; the **role is decided by each Task's prompt** (proof-checker / research-review prompts live on the skill side).
- The name is the family declaration: `gpt-reviewer` states the default GPT family, cross-family with the executor (Claude), satisfying the upstream same-origin check (`provenance.py model_family()` maps the `gpt` needle to the openai family). If you switch families, rename accordingly (e.g. `deepseek-reviewer`).
- Tools are read-only plus lookup: `Read/Glob/Grep` read files, `Bash/WebFetch/WebSearch` run read-only commands and external lookups (e.g. citation checks, slide rendering). `TodoWrite` is subagent-local planning and never touches the main agent's todos. `Edit/Write` stay off — the reviewer judges, never edits.
- To pin a reviewer model, append a `model: ...` line to that file yourself; omit it to follow the ZCode default.
- 29 skills that invoke external review (the machine-scanned full set of skills containing an `mcp__codex__codex:` call block, plus orchestrator-type `experiment-audit`) get an **idempotently appended** branch after hard copy:

  ```md
  <!-- ZCODE:GPT-REVIEWER -->
  **If `Task(agent_type: gpt-reviewer)` is available (ZCode default):**
    Use `Task(agent_type: gpt-reviewer, prompt: "<same prompt that would go to codex>")`.
    No model or reasoning parameters needed per call — the reviewer model is treated as sharing the agent's name.
    Hard `mcp__codex__codex` call remains as fallback when `gpt-reviewer` is unavailable.
  ```

  `gpt-reviewer` is preferred; `mcp__codex__codex` remains as fallback. Vendor files are never modified. Legacy `aris-agent` / `ZCODE:ARIS-AGENT / ZCODE:GPT-WORKER (legacy, auto-migrated)` markers are migrated in place on reinstall, never appended twice.

### 3.3 Dual Paths: `.zcode` vs `.aris`

- **`.zcode/skills`, `.zcode/agents`** — follow ZCode discovery and precedence (`~/.zcode/skills` > `~/.agents/skills` > `<repo>/.zcode/skills` > plugins; path is identity, first same-named match wins).
- **`.aris/tools`, `.aris/installed-skills.txt`, `~/.aris/repo`** — keep ARIS-native paths so the Canonical Helper resolution chain inside `SKILL.md` works unchanged:

  ```bash
  # Resolution excerpt inside SKILLs
  HELPER=".aris/tools/<helper>"; [ -f "$HELPER" ] || HELPER="tools/<helper>"
  [ -f "$HELPER" ] || { [ -n "${ARIS_REPO:-}" ] && HELPER="$ARIS_REPO/tools/<helper>"; }
  # ARIS_REPO comes from .aris/installed-skills.txt (repo_root) or ~/.aris/repo
  ```

  Templates live at the project-root `templates/` (hard copy, see §2); `$ARIS_REPO/templates/XXX` remains as fallback. Shared references under `.zcode/skills/shared-references/` are loaded on demand when a SKILL cites them — they are not auto-injected.

### 3.4 AGENTS.md Managed Block

`init.py` only maintains the `<!-- ARIS:BEGIN --> ... <!-- ARIS:END -->` block — no `# Project: <dirname>` titles, no Pipeline Status. Users are free to add their own conventions around the block.

---

## 4. Migration Status

| Capability | Status | Notes |
|------|------|------|
| Skills (86) hard-copied to `.zcode/skills` | ✅ Done | Whitelist distribution, `--groups`/`--skills`/`--exclude` filtering, idempotent |
| Tools hard-copied to `.aris/tools` | ✅ Done | Includes `meta_opt/`, `corpus_write_guard.py`, etc.; `--link` switches to symlinks |
| Agents (`gpt-reviewer`) | ✅ Done | Single generic agent; name is the family declaration; prompt decides the role |
| AGENTS.md managed block | ✅ Done | Compare-and-swap, managed block only |
| Reviewer-branch patches (29 skills) | ✅ Done | Appends `gpt-reviewer` branch, `mcp__codex__codex` kept as fallback |
| Manifest / global pointer | ✅ Done | `.aris/installed-skills.txt` + `~/.aris/repo` support incremental alignment and `$ARIS_REPO` resolution |
| **Hooks** | **🚧 Not migrated** | **See below** |
| MCP servers (codex / llm-chat / gemini-review, etc.) | ⏳ To verify | Workspace MCP now auto-connects in ZCode, but this port has not been verified end-to-end in a live ZCode session |
| Plugins (workspace scope) | ⚠️ Limited | ZCode workspace does not support plugins, only global ones; ARIS plugin-style hooks must go the config-file route |

### Hooks — Not Migrated (main flow unaffected)

Upstream registers hooks via `templates/claude-hooks/meta_logging.json` (`PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `SessionStart`, `SessionEnd`) and `corpus_write_guard.json` (`PreToolUse: Bash`), with scripts at `tools/meta_opt/log_event.sh`, `check_ready.sh`, `corpus_write_guard.py`.

ZCode differences requiring adaptation:

- Config-file hooks are **disabled by default**; `hooks.enabled: true` is mandatory (see `zcode-configuration-guide` / `diagnosing-hooks`).
- Only 7 events are supported: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Stop`. Upstream `SessionEnd` must map to `Stop`.
- Template variable `CLAUDE_PROJECT_DIR` must become `ZCODE_PROJECT_DIR`; the `async` field currently has no runtime effect.
- The GUI's whole-file write to `.zcode/config.json` overwrites file-based hooks (observed: adding two test hooks via GUI replaced the existing 6 events with 2), requiring a per-`(matcher, command)` dedup merge strategy.
- Verification is via file content and the `.aris/meta/events.jsonl` event log, not the GUI's "installed N" count (which only counts GUI-added interactive hooks).

`init.py:ensure_hooks()` already implements a first version (mapping `CLAUDE_PROJECT_DIR→ZCODE_PROJECT_DIR`, `SessionEnd→Stop`, writing `hooks.enabled/events` into `.zcode/config.json` with incremental merge), but it has not been integration-tested through live ZCode session lifecycle triggers and log persistence, so this README marks it as **not migrated**. The skills main flow works without it; audit/traceback capabilities await the integration pass.

---

## 5. Upstream Updates

```bash
# 1) Update the vendor
cd Auto-claude-code-research-in-sleep && git pull

# 2) Re-align every paper project (run from the wrapper root)
python init.py D:/AIWorkSpace/my-paper --reconcile        # interactive for new skills
python init.py D:/AIWorkSpace/my-paper --reconcile --add-new   # auto-accept new skills
python init.py D:/AIWorkSpace/my-paper --reconcile --skip-new  # auto-skip new skills
```

If a paper project has locally modified a `SKILL.md`, re-alignment never silently overwrites it; conflicts require explicit handling.

---

## 6. Notes

- Never edit files under `Auto-claude-code-research-in-sleep/skills/` directly; edit `.zcode/skills/<name>/SKILL.md` in the derived paper project instead.
- ZCode config precedence: user scope (`~/.zcode/skills`) > workspace (`<repo>/.zcode/skills`) > plugins. Same-named skills shadow; only the first match loads.
- Templates live at the project-root `templates/` hard copy (see §2). Shared references under `.zcode/skills/shared-references/` resolve on demand when a SKILL cites them; `$ARIS_REPO` (manifest `repo_root` + `~/.aris/repo`) remains as helper fallback, not the primary template path.
- The review flow defaults to `gpt-reviewer`, falling back to `mcp__codex__codex` when that agent is unavailable. For the `llm-chat` generic gateway or `manual-review`, follow each skill's `REVIEWER_BACKEND` option.
- While hooks remain unmigrated, `meta-optimize`'s passive logging and `corpus_write_guard`'s Bash-write interception are inactive; the main flow works, audit capabilities pending.

---

## 7. References

- Upstream docs: `Auto-claude-code-research-in-sleep/README.md`, `AGENT_GUIDE.md`, `docs/ARIS_INTRO.md`
- ZCode config: `zcode-configuration-guide` (5 resource types, discovery order, merge rules), `diagnosing-hooks` (hooks troubleshooting)
- Key scripts: `init.py` (distribution and alignment), `Auto-claude-code-research-in-sleep/tools/install_aris.sh` (upstream installer for reference)
