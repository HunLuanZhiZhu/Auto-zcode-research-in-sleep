#!/usr/bin/env python3
"""
install_aris_zcode.py — Project-local ARIS skill installation for ZCode

Copies each ARIS skill as a real directory so the paper project owns its
own copy and can be modified independently (hard copy, not symlink):

  <project>/.zcode/skills/<skill-name>  (copy of <zcode-aris>/skills/<skill-name>)

Managed entries are tracked in .aris/installed-skills.txt. The script never
replaces real files or user-owned skill directories; conflicts must be resolved
explicitly. Also copies .aris/tools from vendor/tools for the Canonical
Helper chain (Layer 1) and copies .zcode/agents/gpt-reviewer.md.

This is the ZCode port of tools/install_aris.sh — skills/agents are .zcode
(ZCode-native), tools/manifest/global-pointer are .aris (ARIS-native, as SKILL.md expects).
Default is hard copy; use --link for symlink/junction projection.

Usage:
  python install_aris_zcode.py [project_path] [options]

Actions (mutually exclusive, default: auto):
  default          install if no manifest, else reconcile
  --reconcile      explicit reconcile; refuse if no manifest
  --uninstall      remove only entries in manifest
  --list-groups    print the group catalog and exit

Selection (catalog: tools/skill-groups.tsv):
  --groups A,B     install only these skill groups
  --skills X,Y     additionally install these skills
  --exclude X,Y    never install these skills (recorded as declined)
  --all            install every upstream skill (legacy default)
  --add-new        reconcile: accept all upstream skills not yet installed
  --skip-new       reconcile: skip new upstream skills without prompting
  With no selection flags: --all is the default (install everything).

Options:
  --aris-repo PATH  override zcode-aris repo discovery (default: script dir)
  --dry-run         show plan, no writes
  --quiet           no prompts; abort on conflict
  --reconcile / --uninstall see above
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

MANIFEST_VERSION = "1"
MANIFEST_NAME = "installed-skills.txt"
DECLINED_NAME = "skills-declined.txt"
CATALOG_REL = Path("tools/skill-groups.tsv")
GLOBAL_POINTER = Path.home() / ".aris" / "repo"
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUPPORT_NAMES = ["shared-references"]
EXCLUDE_TOP_NAMES = {"skills-codex", "skills-codex.bak"}
SKILLS_SUBDIR = "skills"
ZCODE_SKILLS_REL = Path(".zcode/skills")
ZCODE_AGENTS_REL = Path(".zcode/agents")
ZCODE_DIR_NAME = ".zcode"
ARIS_TOOLS_LINK = Path(".aris/tools")  # copy of repo/tools — ARIS-native, not ZCode-native
LINK_MODE = False  # set in main() from --link
BLOCK_BEGIN = "<!-- ARIS:BEGIN -->"
BLOCK_END = "<!-- ARIS:END -->"
DOC_FILE_NAME = "AGENTS.md"


# ------------------------------------------------------------------ helpers

def log(msg: str, quiet: bool = False):
    if not quiet:
        print(msg)

def warn(msg: str):
    print(f"warning: {msg}", file=sys.stderr)

def die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)

def is_safe_name(name: str) -> bool:
    return bool(SAFE_NAME_RE.match(name))

def is_symlink(p: Path) -> bool:
    return p.is_symlink()

def read_link_target(p: Path) -> str:
    try:
        return os.readlink(p)
    except OSError:
        return ""

def canonicalize(p: Path) -> Path:
    try:
        return p.resolve()
    except Exception:
        return p

def abs_path(p: Path) -> Path:
    return p.resolve()

# create link or hard copy depending on LINK_MODE
def create_link(target: Path, link: Path):
    link.parent.mkdir(parents=True, exist_ok=True)
    if LINK_MODE:
        # symlink/junction projection (original behavior)
        if sys.platform == "win32":
            if target.is_dir():
                import subprocess
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                try:
                    os.symlink(str(target), str(link))
                except OSError:
                    shutil.copy2(target, link)
        else:
            os.symlink(str(target), str(link))
    else:
        # hard copy — paper project owns its copy and can modify independently
        if target.is_dir():
            shutil.copytree(target, link)
        else:
            shutil.copy2(target, link)

def remove_link(p: Path):
    if p.is_symlink():
        p.unlink()
    elif p.is_dir():
        # junction or hard-copied directory
        try:
            # try junction unlink first
            p.unlink()
        except Exception:
            pass
        if p.exists():
            # hard copy — remove recursively
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    import subprocess
                    subprocess.run(["cmd", "/c", "rmdir", "/S", "/Q", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
    elif p.is_file():
        try:
            p.unlink()
        except Exception:
            pass

# ------------------------------------------------------------------ repo discovery

def resolve_wrapper_repo(override: str | None) -> Path:
    if override:
        p = Path(override).expanduser().resolve()
        if not (p / "skills").is_dir():
            die(f"--aris-repo has no skills/ subdir: {p}")
        return p
    # script dir — wrapper root has vendor subdir, not skills directly
    script_dir = Path(__file__).parent.resolve()
    vendor = script_dir / "Auto-claude-code-research-in-sleep"
    if (vendor / "skills").is_dir():
        return vendor
    if (script_dir / "skills").is_dir():
        return script_dir
    # env var
    env = os.environ.get("ZCODE_ARIS_REPO") or os.environ.get("ARIS_REPO")
    if env and Path(env).expanduser().is_dir() and (Path(env).expanduser() / "skills").is_dir():
        return Path(env).expanduser().resolve()
    # guesses
    for guess in [Path.home() / "zcode-aris", Path.home() / "Auto-zcode-research-in-sleep"]:
        if (guess / "skills").is_dir():
            return guess.resolve()
    die("cannot find ZCode-ARIS repo. Use --aris-repo PATH or set ZCODE_ARIS_REPO env var.")

# ------------------------------------------------------------------ inventory

def build_upstream_inventory(repo: Path):
    entries = []  # list of (kind, name)
    skills_dir = repo / "skills"
    for d in skills_dir.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        if not is_safe_name(name):
            warn(f"skipping unsafe upstream name: {name}")
            continue
        if name in EXCLUDE_TOP_NAMES:
            continue
        if name in SUPPORT_NAMES:
            continue
        if not (d / "SKILL.md").is_file():
            continue
        # S10: source must not be symlink outside repo
        if is_symlink(d):
            resolved = canonicalize(d)
            try:
                resolved.relative_to(repo)
            except ValueError:
                warn(f"skipping upstream symlink leading outside repo: {name} -> {resolved}")
                continue
        entries.append(("skill", name))
    for s in SUPPORT_NAMES:
        if (skills_dir / s).is_dir():
            entries.append(("support", s))
    return entries

# ------------------------------------------------------------------ catalog

def _load_catalog_lines(catalog_path: Path):
    if not catalog_path.is_file():
        return []
    return catalog_path.read_text(encoding="utf-8").splitlines()

def catalog_groups(catalog_path: Path):
    for line in _load_catalog_lines(catalog_path):
        if line.startswith("group\t"):
            parts = line.split("\t")
            if len(parts) >= 2:
                yield parts[1], parts[2] if len(parts) > 2 else "", parts[3] if len(parts) > 3 else ""

def catalog_skills_in_group(catalog_path: Path, gid: str):
    for line in _load_catalog_lines(catalog_path):
        if line.startswith("skill\t"):
            parts = line.split("\t")
            if len(parts) >= 3 and parts[2] == gid:
                yield parts[1]

def catalog_has_skill(catalog_path: Path, skill: str) -> bool:
    for line in _load_catalog_lines(catalog_path):
        if line.startswith("skill\t") and line.split("\t")[1] == skill:
            return True
    return False

def catalog_requires(catalog_path: Path, skill: str):
    for line in _load_catalog_lines(catalog_path):
        if line.startswith("skill\t"):
            parts = line.split("\t")
            if parts[1] == skill:
                if len(parts) >= 4 and parts[3] != "-":
                    return [x.strip() for x in parts[3].split(",") if x.strip()]
                return []
    return []

def print_group_catalog(catalog_path: Path, repo: Path):
    if not catalog_path.is_file():
        die(f"skill catalog not found: {catalog_path}")
    print(f"Skill groups (from {catalog_path}):\n")
    for gid, display, desc in catalog_groups(catalog_path):
        # count
        n = sum(1 for _ in catalog_skills_in_group(catalog_path, gid))
        print(f"  {gid:14s} {display} — {desc}  [{n} skills]")
        for line in _load_catalog_lines(catalog_path):
            if line.startswith("skill\t"):
                p = line.split("\t")
                if len(p) >= 3 and p[2] == gid:
                    short = p[4] if len(p) >= 5 else ""
                    print(f"      {p[1]:28s} {short}")

# ------------------------------------------------------------------ manifest

def load_manifest(manifest_path: Path):
    if not manifest_path.is_file():
        return []
    text = manifest_path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    # find header
    try:
        idx = lines.index("kind\tname\tsource_rel\ttarget_rel\tmode")
    except ValueError:
        return []
    rows = []
    for line in lines[idx+1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) == 5:
            rows.append(tuple(parts))
    return rows

def manifest_names(rows):
    return [r[1] for r in rows]

def load_declined(declined_path: Path) -> set[str]:
    if not declined_path.is_file():
        return set()
    return {l.strip() for l in declined_path.read_text(encoding="utf-8").splitlines() if l.strip()}

# ------------------------------------------------------------------ selection

def build_selection(upstream, catalog_path: Path, manifest_rows, args, declined_set: set[str], repo: Path):
    # upstream: list of (kind, name)
    upstream_skill_names = {n for k, n in upstream if k == "skill"}
    selected: set[str] = set()
    declined_candidates: set[str] = set(declined_set)

    # parse --exclude
    excludes: set[str] = set()
    if args.exclude:
        for x in args.exclude.split(","):
            x = x.strip()
            if x:
                excludes.add(x)
                declined_candidates.add(x)

    has_selection_flags = bool(args.groups or args.skills)
    is_fresh = not manifest_rows  # no manifest

    # validate
    if args.groups:
        if not catalog_path.is_file():
            die(f"--groups needs catalog at {catalog_path}")
        for g in args.groups.split(","):
            g = g.strip()
            if not g:
                continue
            found = any(gid == g for gid, _, _ in catalog_groups(catalog_path))
            if not found:
                die(f"unknown group '{g}' — run with --list-groups")

    if args.skills:
        for s in args.skills.split(","):
            s = s.strip()
            if not s:
                continue
            if s not in upstream_skill_names:
                die(f"unknown skill '{s}' (not an upstream skill)")

    if is_fresh:
        if args.all_flag or (not has_selection_flags and (args.quiet or not sys.stdin.isatty())):
            selected = set(upstream_skill_names)
        elif has_selection_flags:
            if args.groups:
                for g in args.groups.split(","):
                    g = g.strip()
                    if not g:
                        continue
                    for name in catalog_skills_in_group(catalog_path, g):
                        if name in upstream_skill_names:
                            selected.add(name)
            if args.skills:
                for s in args.skills.split(","):
                    s = s.strip()
                    if s:
                        selected.add(s)
            # remember unselected as declined when explicit subset
            for n in upstream_skill_names:
                if n not in selected:
                    declined_candidates.add(n)
        else:
            # interactive not implemented — default to all
            log("No selection flags and TTY — defaulting to --all (install everything). Use --groups/--skills to select.")
            selected = set(upstream_skill_names)
    else:
        # reconcile: installed set = manifest ∩ upstream
        for kind, name, *_ in manifest_rows:
            if kind == "skill" and name in upstream_skill_names:
                selected.add(name)
        # flag-based additions re-enable declined
        if args.groups:
            for g in args.groups.split(","):
                g = g.strip()
                if not g:
                    continue
                for name in catalog_skills_in_group(catalog_path, g):
                    if name in upstream_skill_names:
                        selected.add(name)
                        declined_candidates.discard(name)
        if args.skills:
            for s in args.skills.split(","):
                s = s.strip()
                if s and s in upstream_skill_names:
                    selected.add(s)
                    declined_candidates.discard(s)
        # new upstream skills
        new_skills = [n for n in upstream_skill_names if n not in selected and n not in declined_candidates]
        if new_skills:
            if args.all_flag or args.add_new:
                selected.update(new_skills)
                log(f"→ adding {len(new_skills)} new upstream skill(s) (--all/--add-new): {', '.join(sorted(new_skills))}")
            elif args.skip_new or args.quiet or not sys.stdin.isatty():
                warn(f"new upstream skills NOT installed: {', '.join(sorted(new_skills))}  (rerun with --add-new or --skills)")
            else:
                # prompt per skill
                print("\nNew skills appeared upstream since last install:")
                for name in sorted(new_skills):
                    ans = input(f"  install new skill {name:30s} [y/N] ").strip().lower()
                    if ans in ("y", "yes"):
                        selected.add(name)
                    else:
                        declined_candidates.add(name)

    # excludes beat everything (before dep expansion)
    selected = {s for s in selected if s not in excludes}

    # expand deps transitively
    if catalog_path.is_file():
        changed = True
        while changed:
            changed = False
            for name in list(selected):
                for dep in catalog_requires(catalog_path, name):
                    if dep in selected:
                        continue
                    if dep in excludes:
                        warn(f"'{name}' requires '{dep}' but it is excluded — that pipeline phase will break")
                        continue
                    if dep not in upstream_skill_names:
                        continue
                    selected.add(dep)
                    log(f"  ↳ auto-including '{dep}' (required by '{name}')")
                    changed = True

    if not selected:
        die("selection is empty — nothing to install (use --all or --groups/--skills)")

    return selected, declined_candidates


def filter_upstream_by_selection(upstream, selected: set[str]):
    out = []
    for kind, name in upstream:
        if kind == "support":
            out.append((kind, name))
        elif kind == "skill" and name in selected:
            out.append((kind, name))
    return out

# ------------------------------------------------------------------ plan

def compute_plan(upstream_filtered, manifest_rows, project_skills_dir: Path, repo: Path, repo_skills_abs: Path):
    # return list of (ACTION, kind, name, extra)
    # ACTION: CREATE | REUSE | ADOPT | UPDATE_TARGET | CONFLICT | REMOVE
    manifest_map = {name: (kind, src, tgt, mode) for kind, name, src, tgt, mode in manifest_rows}
    plan = []
    for kind, name in upstream_filtered:
        target = project_skills_dir / name
        expected = repo_skills_abs / name
        if target.is_symlink() or (sys.platform == "win32" and target.is_dir() and target.exists()):
            # check if it's a junction/symlink to expected
            cur_str = read_link_target(target)
            # On Windows junction, readlink may return empty; try to resolve
            cur = Path(cur_str) if cur_str else None
            # is_symlink already true for junction; but canonicalize both
            try:
                cur_resolved = canonicalize(target) if not cur_str else (Path(cur_str).resolve() if Path(cur_str).is_absolute() else canonicalize(target.parent / cur_str))
            except Exception:
                cur_resolved = None
            exp_resolved = canonicalize(expected)
            is_managed = name in manifest_map
            if cur_resolved == exp_resolved or (cur_str and Path(cur_str) == expected):
                if is_managed:
                    plan.append(("REUSE", kind, name, ""))
                else:
                    plan.append(("ADOPT", kind, name, ""))
            else:
                if is_managed:
                    plan.append(("UPDATE_TARGET", kind, name, cur_str or str(cur_resolved)))
                else:
                    plan.append(("CONFLICT", kind, name, f"symlink_to:{cur_str or cur_resolved}"))
        elif target.exists():
            plan.append(("CONFLICT", kind, name, "real_path"))
        else:
            plan.append(("CREATE", kind, name, ""))
    # manifest entries no longer in upstream -> REMOVE
    upstream_names = {n for _, n in upstream_filtered}
    for kind, name, src, tgt, mode in manifest_rows:
        if name not in upstream_names:
            plan.append(("REMOVE", kind, name, ""))
    return plan


def print_plan(plan):
    from collections import Counter
    c = Counter(a for a, *_ in plan)
    print("\nPlan summary:")
    print(f"  CREATE:        {c['CREATE']}  (new links to add)")
    print(f"  ADOPT:         {c['ADOPT']}   (orphan links already pointing correctly)")
    print(f"  UPDATE_TARGET: {c['UPDATE_TARGET']}  (managed links with stale target)")
    print(f"  REUSE:         {c['REUSE']}  (already correct, no-op)")
    print(f"  REMOVE:        {c['REMOVE']}  (in old manifest, no longer upstream)")
    print(f"  CONFLICT:      {c['CONFLICT']}  (must be resolved)")
    if c["CONFLICT"]:
        print("\nConflicts (need user action):")
        for a, k, n, e in plan:
            if a == "CONFLICT":
                print(f"  - {n} ({k}): {e}")

# ------------------------------------------------------------------ apply

def apply_plan(plan, manifest_tmp: Path, project_skills_dir: Path, repo_skills_abs: Path, dry_run: bool, quiet: bool):
    project_skills_dir.mkdir(parents=True, exist_ok=True)
    for action, kind, name, extra in plan:
        target = project_skills_dir / name
        expected = repo_skills_abs / name
        if action in ("REUSE", "ADOPT"):
            continue
        elif action == "CREATE":
            if target.exists() or is_symlink(target):
                die(f"S4 violation: {target} appeared between plan and apply")
            if dry_run:
                print(f"  (dry-run) link {name} -> {expected}")
            else:
                create_link(expected, target)
                print(f"  + {name}")
        elif action == "UPDATE_TARGET":
            # S11 + S2 checks simplified
            cur_str = read_link_target(target)
            if dry_run:
                print(f"  (dry-run) update {name} -> {expected}")
            else:
                # remove old
                try:
                    remove_link(target)
                except Exception:
                    pass
                create_link(expected, target)
                print(f"  ↻ {name}")
        elif action == "REMOVE":
            # S1: must be symlink
            if not is_symlink(target) and not target.exists():
                continue
            if not is_symlink(target):
                warn(f"S1: {target} is not a symlink, refusing to remove")
                continue
            cur_str = read_link_target(target)
            # S2: target must be inside repo (simplified)
            if dry_run:
                print(f"  (dry-run) rm {name}")
            else:
                remove_link(target)
                print(f"  - {name}")
        elif action == "CONFLICT":
            die(f"BUG: CONFLICT {name} reached apply phase")


def write_manifest_tmp(plan, out_path: Path, repo: Path, project_path: Path):
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"version\t{MANIFEST_VERSION}\n")
        f.write(f"repo_root\t{repo}\n")
        f.write(f"project_root\t{project_path}\n")
        f.write(f"generated\t{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
        f.write("kind\tname\tsource_rel\ttarget_rel\tmode\n")
        for action, kind, name, _ in plan:
            if action in ("REUSE", "ADOPT", "CREATE", "UPDATE_TARGET"):
                mode = "symlink" if LINK_MODE else "copy"
                f.write(f"{kind}\t{name}\tskills/{name}\t.zcode/skills/{name}\t{mode}\n")


def ensure_tools_symlink(project_path: Path, repo: Path, dry_run: bool):
    link = project_path / ".aris" / "tools"
    expected = repo / "tools"
    if LINK_MODE:
        if is_symlink(link):
            cur = read_link_target(link)
            try:
                cur_p = Path(cur).resolve() if cur and Path(cur).is_absolute() else (link.parent / cur).resolve() if cur else None
            except Exception:
                cur_p = None
            if cur_p == expected.resolve():
                return
            warn(f".aris/tools already exists with different target ({cur}); leaving alone")
            return
        if link.exists():
            warn(f".aris/tools already exists as non-symlink; leaving alone")
            return
        if dry_run:
            print(f"  (dry-run) link .aris/tools -> {expected}")
        else:
            link.parent.mkdir(parents=True, exist_ok=True)
            create_link(expected, link)
            print(f"  + .aris/tools -> tools/")
    else:
        # hard copy mode — .aris/tools is a real directory
        if link.exists():
            if dry_run:
                print(f"  (dry-run) .aris/tools already exists, skipping")
            return
        if dry_run:
            print(f"  (dry-run) copy .aris/tools <- {expected}")
        else:
            link.parent.mkdir(parents=True, exist_ok=True)
            create_link(expected, link)
            print(f"  + .aris/tools (copy)")

def remove_tools_symlink(project_path: Path, repo: Path, dry_run: bool):
    link = project_path / ".aris" / "tools"
    expected = repo / "tools"
    if LINK_MODE:
        if not is_symlink(link):
            return
        cur = read_link_target(link)
        try:
            cur_p = Path(cur).resolve() if cur and Path(cur).is_absolute() else (link.parent / cur).resolve() if cur else None
        except Exception:
            return
        if cur_p != expected.resolve():
            return
        if dry_run:
            print(f"  (dry-run) rm .aris/tools")
        else:
            remove_link(link)
            print(f"  - .aris/tools")
    else:
        # hard copy — remove directory if it exists
        if not link.exists():
            return
        if dry_run:
            print(f"  (dry-run) rm .aris/tools (copy)")
        else:
            remove_link(link)
            print(f"  - .aris/tools (copy)")

def _wrapper_root() -> Path:
    return Path(__file__).parent.resolve()

def ensure_agents(project_path: Path, repo: Path, dry_run: bool):
    # agents live in wrapper, not vendor
    src_dir = _wrapper_root() / ".zcode" / "agents"
    if not src_dir.is_dir():
        # fallback: vendor (in case user moved agents there)
        src_dir = repo / ".zcode" / "agents"
        if not src_dir.is_dir():
            return
    dst_dir = project_path / ".zcode" / "agents"
    # Remove legacy agent files left by older installs (renamed to gpt-worker.md).
    for legacy_name in ("aris-agent.md", "gpt-reviewer.md"):
        legacy = dst_dir / legacy_name
        if legacy.is_file() or is_symlink(legacy):
            if dry_run:
                print(f"  (dry-run) rm .zcode/agents/{legacy_name} (legacy, renamed to gpt-worker.md)")
            else:
                try:
                    remove_link(legacy)
                    print(f"  - .zcode/agents/{legacy_name} (legacy, renamed to gpt-worker.md)")
                except Exception as e:
                    warn(f"cannot remove legacy .zcode/agents/{legacy_name}: {e}")
    for src in src_dir.glob("*.md"):
        if not src.is_file():
            continue
        # skip symlink outside
        try:
            resolved = src.resolve()
            if not str(resolved).startswith(str(src_dir.resolve())):
                warn(f"skipping external symlink: {src}")
                continue
        except Exception:
            pass
        name = src.name
        dst = dst_dir / name
        if is_symlink(dst):
            cur = read_link_target(dst)
            # check correct
            if cur and Path(cur) == src:
                continue
            warn(f".zcode/agents/{name} already exists with different target; leaving alone")
            continue
        if dst.exists():
            warn(f".zcode/agents/{name} already exists as non-symlink; leaving alone")
            continue
        if dry_run:
            print(f"  (dry-run) link .zcode/agents/{name}")
        else:
            dst_dir.mkdir(parents=True, exist_ok=True)
            create_link(src, dst)
            print(f"  + .zcode/agents/{name}")


def update_agents_md(project_path: Path, repo: Path, installed_names: list[str], dry_run: bool, no_doc: bool):
    """ZCode equivalent of install_aris.sh:update_claude_doc — manages AGENTS.md block.
    Only creates/updates the managed block; does not add Pipeline Status or titles.
    User can edit AGENTS.md afterwards."""
    doc_file = project_path / DOC_FILE_NAME
    if no_doc:
        return
    if not doc_file.is_file():
        if dry_run:
            print(f"  (dry-run) would create {DOC_FILE_NAME} with ARIS managed block")
            return
        doc_file.write_text("", encoding="utf-8")
        original = ""
    else:
        try:
            original = doc_file.read_text(encoding="utf-8")
        except Exception as e:
            warn(f"{DOC_FILE_NAME} read failed: {e}")
            return
    # Now update/insert managed block
    try:
        # reuse original already read above — just ensure it's defined
        _ = original
    except NameError:
        original = ""
    except Exception as e:
        warn(f"{DOC_FILE_NAME} read failed: {e}")
        return
    count = len(installed_names)
    # Determine update command for ZCode
    new_block = (
        f"{BLOCK_BEGIN}\n"
        f"## ARIS Skill Scope (ZCode)\n"
        f"ARIS skills installed in this project: {count} entries.\n"
        f"Manifest: `.aris/{MANIFEST_NAME}` (lists every skill and its upstream target).\n"
        f"For ARIS workflows, prefer the project-local skills under `.zcode/skills/` over global skills.\n"
        f"Reviewer routing: under ZCode, always prefer `Task(agent_type: gpt-worker)` (model and reasoning already configured) over Codex MCP; use Codex MCP only when the user explicitly requests it or when `gpt-worker` is unavailable.\n"
        f"Long-run rule (ZCode-only): any task expected to exceed ~10 minutes (e.g. model training, large sweeps) MUST run via `Bash(run_in_background: true)`; either rely on the tool's persisted output log or redirect stdout/stderr to a `logs/` file yourself — never leave output only in the live session. Every log line MUST carry a wall-clock timestamp precise to the second (`%Y-%m-%d %H:%M:%S`); the main agent judges background-task state by reading the log tail, not by the session being alive.\n"
        f"Do not modify or delete files inside any skill that is a symlink (symlinks point into `{repo}`).\n"
        f"Update with: `python {repo.parent / 'init.py'} --reconcile`  (re-runnable; reconciles new/removed skills).\n"
        f"{BLOCK_END}"
    )
    if BLOCK_BEGIN in original:
        # Replace existing block — use regex like original (DOTALL)
        pattern = re.compile(re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END), re.DOTALL)
        matches = pattern.findall(original)
        if len(matches) > 1:
            warn(f"multiple ARIS blocks found in {DOC_FILE_NAME}; skipping update")
            return
        new_content = pattern.sub(lambda _: new_block, original)
    else:
        new_content = original
        if new_content and not new_content.endswith("\n"):
            new_content += "\n"
        new_content += new_block + "\n"
    if new_content == original:
        return
    if dry_run:
        print(f"  (dry-run) would update {DOC_FILE_NAME} ARIS block ({count} skills)")
        return
    # Compare-and-swap: re-read, only commit if unchanged
    tmp = doc_file.with_suffix(doc_file.suffix + ".aris-tmp")
    tmp.write_text(new_content, encoding="utf-8")
    try:
        current = doc_file.read_text(encoding="utf-8")
    except Exception:
        current = ""
    if current != original:
        tmp.unlink(missing_ok=True)
        warn(f"{DOC_FILE_NAME} changed during install — skipping doc update (rerun to retry)")
        return
    tmp.replace(doc_file)
    print(f"  ✓ updated {DOC_FILE_NAME} (ARIS managed block, {count} skills)")


ZCODE_PATCH_MARKER = "<!-- ZCODE:GPT-WORKER -->"
LEGACY_PATCH_MARKERS = ("<!-- ZCODE:ARIS-AGENT -->", "<!-- ZCODE:GPT-REVIEWER -->")

# Skills that directly invoke an external reviewer (contain an `mcp__codex__codex:`
# call block) — these get an additional gpt-worker branch. Plus experiment-audit
# (orchestrator-type, kept from the original 10 for continuity).
# Machine-scanned from vendor/skills: every dir with an `mcp__codex__codex:` block.
PATCH_TARGET_SKILLS = [
    "research-review",
    "proof-checker",
    "experiment-audit",
    "paper-claim-audit",
    "citation-audit",
    "kill-argument",
    "rebuttal",
    "idea-creator",
    "auto-review-loop",
    "research-refine",
    "paper-writing",
    "paper-plan",
    "paper-write",
    "paper-figure",
    "auto-paper-improvement-loop",
    "paper-slides",
    "slides-polish",
    "claims-drafting",
    "patent-novelty-check",
    "patent-review",
    "invention-structuring",
    "specification-writing",
    "experiment-bridge",
    "ablation-planner",
    "result-to-claim",
    "figure-spec",
    "novelty-check",
    "training-check",
    "meta-optimize",
]


def _body_start(text: str) -> int:
    """Offset where the SKILL.md body starts (after the frontmatter close).

    Frontmatter is `---` on line 1 through the next `---` line. Markers found
    before that (e.g. in the allowed-tools line) must not be patch targets.
    Returns 0 when no frontmatter is detected.
    """
    if not text.startswith("---"):
        return 0
    nl = text.find("\n")
    if nl == -1:
        return 0
    close = text.find("\n---", nl)
    if close == -1:
        return 0
    line_end = text.find("\n", close + 1)
    return line_end + 1 if line_end != -1 else len(text)


def _gpt_worker_block() -> str:
    return (
        f"\n{ZCODE_PATCH_MARKER}\n"
        f"**If `Task(agent_type: gpt-worker)` is available (ZCode default):**\n"
        f"  Use `Task(agent_type: gpt-worker, prompt: \"<same prompt that would go to codex>\")`.\n"
        f"  No model or reasoning parameters needed per call — the reviewer model is treated as sharing the agent's name.\n"
        f"  Hard `mcp__codex__codex` call remains as fallback when `gpt-worker` is unavailable.\n"
    )


# Kept under the old name so existing imports keep working.
def _gpt_reviewer_block() -> str:
    return _gpt_worker_block()


def apply_zcode_skill_patches(project_path: Path, dry_run: bool):
    """Inline patches: add gpt-worker branch to the copied SKILL.md files.
    Runs after hard copy, before manifest commit.
    Idempotent: checks for ZCODE_PATCH_MARKER before patching.
    Migrates legacy ZCODE:ARIS-AGENT / ZCODE:GPT-REVIEWER blocks in place.
    Vendor skills are never modified."""
    patched = 0
    skipped = 0
    migrated = 0
    new_block = _gpt_worker_block()
    legacy_blocks = [
        (
            f"\n{LEGACY_PATCH_MARKERS[0]}\n"
            f"**If `Task(agent_type: aris-agent)` is available (ZCode default):**\n"
            f"  Use `Task(agent_type: aris-agent, prompt: \"<same prompt that would go to codex>\")`.\n"
            f"  No model or reasoning parameters needed per call — user has configured the reviewer model independently.\n"
            f"  Hard `mcp__codex__codex` call remains as fallback when `aris-agent` is unavailable.\n"
        ),
        (
            f"\n{LEGACY_PATCH_MARKERS[1]}\n"
            f"**If `Task(agent_type: gpt-reviewer)` is available (ZCode default):**\n"
            f"  Use `Task(agent_type: gpt-reviewer, prompt: \"<same prompt that would go to codex>\")`.\n"
            f"  No model or reasoning parameters needed per call — the reviewer model is treated as sharing the agent's name.\n"
            f"  Hard `mcp__codex__codex` call remains as fallback when `gpt-reviewer` is unavailable.\n"
        ),
    ]
    for skill_name in PATCH_TARGET_SKILLS:
        skill_file = project_path / ".zcode" / "skills" / skill_name / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            text = skill_file.read_text(encoding="utf-8")
        except Exception:
            continue
        if ZCODE_PATCH_MARKER in text:
            skipped += 1
            continue
        migrated_here = False
        for legacy_block in legacy_blocks:
            if legacy_block in text:
                if dry_run:
                    print(f"  (dry-run) would migrate {skill_name}/SKILL.md (-> gpt-worker)")
                    migrated_here = True
                    break
                text = text.replace(legacy_block, new_block, 1)
                skill_file.write_text(text, encoding="utf-8")
                migrated += 1
                migrated_here = True
                break
        if migrated_here:
            continue
        if any(m in text for m in LEGACY_PATCH_MARKERS):
            # Legacy marker present but block text differs (user edited) — leave alone.
            warn(f"{skill_name}/SKILL.md has legacy marker with custom text; leaving alone")
            skipped += 1
            continue
        # Find the Reviewer Calling Convention section and append a gpt-worker branch.
        # Strategy: after the "If REVIEWER_BACKEND = `codex`:" block (or `manual` block),
        # insert an additional ZCode branch. Use a marker so future runs are idempotent.
        patch_block = new_block
        # Insertion point must be AFTER the frontmatter close (second `---` line):
        # markers like mcp__manual_review__review_reply may appear inside the
        # frontmatter's allowed-tools line, and patching there corrupts YAML.
        body_start = _body_start(text)
        # Insertion point: after the first REVIEWER_BACKEND/mcp__codex__codex occurrence, or after Reviewer Calling Convention header.
        inserted = False
        # Prefer inserting after the manual branch if present, otherwise after codex branch
        markers = [
            "mcp__manual_review__review_reply",
            "mcp__manual_review__review",
            "mcp__codex__codex-reply",
            "mcp__codex__codex",
        ]
        for marker in markers:
            idx = text.find(marker, body_start)
            if idx != -1:
                # Find end of that block (next heading or next "If REVIEWER" line)
                # Insert right after the line containing the marker
                line_end = text.find("\n", idx) + 1
                if dry_run:
                    print(f"  (dry-run) would patch {skill_name}/SKILL.md (after {marker})")
                    inserted = True
                    break
                text = text[:line_end] + patch_block + text[line_end:]
                skill_file.write_text(text, encoding="utf-8")
                patched += 1
                inserted = True
                break
        if not inserted:
            # Fallback: append before the next ## heading after Reviewer Calling Convention
            fallback_marker = "## Reviewer Calling Convention"
            idx = text.find(fallback_marker, body_start)
            if idx != -1:
                next_heading = text.find("\n## ", idx + len(fallback_marker))
                insert_at = next_heading if next_heading != -1 else len(text)
                if dry_run:
                    print(f"  (dry-run) would patch {skill_name}/SKILL.md (fallback)")
                else:
                    text = text[:insert_at] + patch_block + text[insert_at:]
                    skill_file.write_text(text, encoding="utf-8")
                    patched += 1
                inserted = True
            else:
                # Last resort: append at end
                if not dry_run:
                    text = text.rstrip() + "\n" + patch_block + "\n"
                    skill_file.write_text(text, encoding="utf-8")
                    patched += 1
                else:
                    print(f"  (dry-run) would patch {skill_name}/SKILL.md (append)")
    if patched or skipped or migrated:
        print(f"  Patched {patched} SKILL.md files for ZCode gpt-worker ({skipped} already patched, {migrated} migrated, {len(PATCH_TARGET_SKILLS)} targets)")


def ensure_global_pointer(repo: Path, dry_run: bool):
    if dry_run:
        return
    try:
        GLOBAL_POINTER.parent.mkdir(parents=True, exist_ok=True)
        cur = ""
        if GLOBAL_POINTER.is_file():
            cur = GLOBAL_POINTER.read_text(encoding="utf-8").strip()
        if cur == str(repo):
            return
        tmp = GLOBAL_POINTER.with_suffix(".tmp")
        tmp.write_text(str(repo) + "\n", encoding="utf-8")
        tmp.replace(GLOBAL_POINTER)
        print(f"  + global pointer {GLOBAL_POINTER} -> {repo}")
    except Exception as e:
        warn(f"cannot write global pointer {GLOBAL_POINTER}: {e}")


def ensure_hooks(project_path: Path, repo: Path, dry_run: bool, no_hooks: bool):
    """ZCode equivalent of templates/claude-hooks — writes .zcode/config.json hooks block.
    Best-effort; skipped with --no-hooks or when hooks already configured.
    Maps: CLAUDE_PROJECT_DIR -> ZCODE_PROJECT_DIR, SessionEnd -> Stop, enables hooks.
    """
    if no_hooks:
        return
    config_path = project_path / ".zcode" / "config.json"
    # Build hooks config for ZCode
    # ZCode hooks format: {hooks: {enabled: true, events: {Event: [{matcher, hooks: [{type, command}]}]}}}
    # Read vendor templates and adapt
    import json
    hooks_events = {}

    # Helper to adapt command string: CLAUDE_PROJECT_DIR -> ZCODE_PROJECT_DIR, SessionEnd handling
    def adapt_cmd(cmd: str) -> str:
        return cmd.replace("CLAUDE_PROJECT_DIR", "ZCODE_PROJECT_DIR")

    # meta_logging: PostToolUse, PostToolUseFailure, UserPromptSubmit, SessionStart, Stop(=SessionEnd)
    # We inline the structure instead of reading vendor template to avoid extra I/O, but keep it in sync
    common_log = 'bash "$ZCODE_PROJECT_DIR/.aris/tools/meta_opt/log_event.sh"'
    hooks_events["PostToolUse"] = [{"matcher": "", "hooks": [{"type": "command", "command": common_log, "timeout": 5}]}]
    hooks_events["PostToolUseFailure"] = [{"matcher": "", "hooks": [{"type": "command", "command": common_log, "timeout": 5}]}]
    hooks_events["UserPromptSubmit"] = [{"hooks": [{"type": "command", "command": common_log, "timeout": 5}]}]
    hooks_events["SessionStart"] = [{"matcher": "", "hooks": [{"type": "command", "command": common_log, "timeout": 5}]}]
    hooks_events["Stop"] = [{"matcher": "", "hooks": [
        {"type": "command", "command": common_log, "timeout": 5},
        {"type": "command", "command": 'bash "$ZCODE_PROJECT_DIR/.aris/tools/meta_opt/check_ready.sh"', "timeout": 5}
    ]}]

    # corpus_write_guard: PreToolUse matcher Bash
    hooks_events["PreToolUse"] = [{"matcher": "Bash", "hooks": [{"type": "command", "command": 'python3 "$ZCODE_PROJECT_DIR/.aris/tools/corpus_write_guard.py"'}]}]

    if dry_run:
        print(f"  (dry-run) would configure hooks in .zcode/config.json ({len(hooks_events)} events)")
        return

    # Read existing config if any
    existing = {}
    if config_path.is_file():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    # Merge: ensure hooks.enabled and hooks.events
    if "hooks" not in existing:
        existing["hooks"] = {}
    existing["hooks"]["enabled"] = True
    if "events" not in existing["hooks"]:
        existing["hooks"]["events"] = {}

    # Merge events: add missing hooks without overwriting user custom hooks
    # If event is new, add all handlers; if event exists, append only handlers with new (matcher, command)
    added = 0
    for event, handlers in hooks_events.items():
        if event not in existing["hooks"]["events"]:
            existing["hooks"]["events"][event] = handlers
            added += len(handlers)
        else:
            # collect existing signatures to avoid duplicates
            existing_sigs = set()
            for eh in existing["hooks"]["events"][event]:
                m = eh.get("matcher", "")
                # hooks is a list with one entry
                cmd = ""
                try:
                    hl = eh.get("hooks", [])
                    if hl:
                        cmd = hl[0].get("command", "")
                except Exception:
                    pass
                existing_sigs.add((m, cmd))
            for h in handlers:
                m = h.get("matcher", "")
                cmd = h.get("hooks", [{}])[0].get("command", "") if h.get("hooks") else ""
                if (m, cmd) not in existing_sigs:
                    existing["hooks"]["events"][event].append(h)
                    existing_sigs.add((m, cmd))
                    added += 1

    if added == 0:
        return  # all hooks already configured

    config_path.parent.mkdir(parents=True, exist_ok=True)
    # atomic write
    tmp = config_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(config_path)
    print(f"  + configured hooks in .zcode/config.json ({added} events, enabled: true)")

# ------------------------------------------------------------------ uninstall

def do_uninstall(project_path: Path, repo: Path, dry_run: bool, quiet: bool):
    manifest_path = project_path / ".aris" / MANIFEST_NAME
    if not manifest_path.is_file():
        die(f"no manifest at {manifest_path}; nothing to uninstall")
    rows = load_manifest(manifest_path)
    print("\nUninstall plan:")
    for kind, name, *_ in rows:
        print(f"  - {name} ({kind})")
    if not dry_run and not quiet:
        ans = input("Proceed? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("aborted")
            sys.exit(0)
    for kind, name, src, tgt, mode in rows:
        target = project_path / tgt
        expected = repo / src
        if not is_symlink(target):
            warn(f"S1: {target} not a symlink, skipping")
            continue
        cur = read_link_target(target)
        # revalidate
        try:
            cur_p = Path(cur).resolve() if cur and Path(cur).is_absolute() else (target.parent / cur).resolve() if cur else None
        except Exception:
            cur_p = None
        if cur_p != expected.resolve() and cur != str(expected):
            warn(f"S8: {target} target {cur} != expected {expected}, skipping")
            continue
        if dry_run:
            print(f"  (dry-run) rm {target}")
        else:
            remove_link(target)
            print(f"  - removed {name}")
    remove_tools_symlink(project_path, repo, dry_run)
    # remove agents we linked
    src_dir = repo / ".zcode" / "agents"
    if src_dir.is_dir():
        for src in src_dir.glob("*.md"):
            dst = project_path / ".zcode" / "agents" / src.name
            if is_symlink(dst) and read_link_target(dst) == str(src):
                if dry_run:
                    print(f"  (dry-run) rm .zcode/agents/{src.name}")
                else:
                    remove_link(dst)
                    print(f"  - removed .zcode/agents/{src.name}")
    if not dry_run:
        # keep .prev
        prev = project_path / ".aris" / (MANIFEST_NAME + ".prev")
        try:
            shutil.copy2(manifest_path, prev)
        except Exception:
            pass
        manifest_path.unlink(missing_ok=True)
        print(f"  ✓ uninstalled (manifest preserved as {prev.name})")

# ------------------------------------------------------------------ main

def main():
    parser = argparse.ArgumentParser(add_help=False, prog="install_aris_zcode.py")
    parser.add_argument("project_path", nargs="?", default="")
    parser.add_argument("--aris-repo", dest="aris_repo", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--groups", default="")
    parser.add_argument("--skills", default="")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--all", dest="all_flag", action="store_true")
    parser.add_argument("--add-new", dest="add_new", action="store_true")
    parser.add_argument("--skip-new", dest="skip_new", action="store_true")
    parser.add_argument("--link", action="store_true", help="use symlink/junction instead of hard copy (default: hard copy)")
    parser.add_argument("--no-hooks", dest="no_hooks", action="store_true", help="skip hooks configuration")
    parser.add_argument("--no-doc", dest="no_doc", action="store_true", help="skip AGENTS.md update")
    parser.add_argument("--list-groups", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args, _unknown = parser.parse_known_args()

    if args.help:
        print(__doc__)
        sys.exit(0)

    if args.all_flag and (args.groups or args.skills):
        die("--all cannot be combined with --groups/--skills (only --exclude)")

    global LINK_MODE
    LINK_MODE = bool(args.link)
    repo = resolve_wrapper_repo(args.aris_repo or None)
    catalog_path = repo / CATALOG_REL

    project_arg = args.project_path or os.getcwd()
    project_path = Path(project_arg).expanduser().resolve()
    if not project_path.is_dir():
        die(f"project path does not exist: {project_path}")

    if args.list_groups:
        print_group_catalog(catalog_path, repo)
        sys.exit(0)

    # uninstall
    if args.uninstall:
        print(f"\nZCode-ARIS Install\n  Project: {project_path}\n  ZCode-ARIS repo: {repo}\n  Action: uninstall{' (dry-run)' if args.dry_run else ''}\n")
        do_uninstall(project_path, repo, args.dry_run, args.quiet)
        sys.exit(0)

    # check reconcile without manifest
    manifest_path = project_path / ".aris" / MANIFEST_NAME
    if args.reconcile and not manifest_path.is_file():
        die(f"--reconcile requires existing manifest; none found at {manifest_path}")

    # check S9: .zcode/.zcode/skills is symlink
    for p in [project_path / ".zcode", project_path / ".zcode" / "skills", project_path / ".aris", project_path / ".aris" / "tools"]:
        if is_symlink(p) and p.exists():
            # .aris/tools being a symlink is expected after first install, don't block on it
            if p == project_path / ".aris" / "tools":
                continue
            die(f"S9: {p} is a symlink — refusing to install (would mutate symlink target)")

    upstream = build_upstream_inventory(repo)
    if not upstream:
        die("upstream inventory empty (broken repo?)")
    manifest_rows = load_manifest(manifest_path)
    declined_path = project_path / ".aris" / DECLINED_NAME
    declined_set = load_declined(declined_path)

    selected, declined_candidates = build_selection(upstream, catalog_path, manifest_rows, args, declined_set, repo)

    # store selection filtering
    upstream_filtered = filter_upstream_by_selection(upstream, selected)

    # compute plan
    repo_skills_abs = repo / "skills"
    project_skills_dir = project_path / ".zcode" / "skills"
    plan = compute_plan(upstream_filtered, manifest_rows, project_skills_dir, repo, repo_skills_abs)
    print(f"\nZCode-ARIS Project Install\n  Project: {project_path}\n  ZCode-ARIS repo: {repo}\n  Action: {'reconcile' if manifest_path.is_file() else 'install'}{' (dry-run)' if args.dry_run else ''}\n")
    print(f"Selection: {len([k for k,n in upstream_filtered if k=='skill'])} of {len([k for k,n in upstream if k=='skill'])} upstream skills")
    print_plan(plan)

    # conflicts
    conflicts = [x for x in plan if x[0] == "CONFLICT"]
    if conflicts:
        print(f"\nAborting due to {len(conflicts)} unresolved conflicts.")
        print("Resolve options per name:\n  - back up & remove the conflicting path manually, then rerun")
        sys.exit(1)

    if args.dry_run:
        ensure_tools_symlink(project_path, repo, dry_run=True)
        # preview hooks
        ensure_hooks(project_path, repo, dry_run=True, no_hooks=args.no_hooks)
        # preview agents
        src_dir = repo / ".zcode" / "agents"
        if src_dir.is_dir():
            for src in src_dir.glob("*.md"):
                dst = project_path / ".zcode" / "agents" / src.name
                if not dst.exists() and not is_symlink(dst):
                    print(f"  (dry-run) link .zcode/agents/{src.name}")
        print("\n(dry-run) no changes made")
        sys.exit(0)

    # confirm
    n_changes = sum(1 for a, *_ in plan if a in ("CREATE", "UPDATE_TARGET", "REMOVE"))
    if n_changes > 0 and not args.quiet:
        # Only prompt if TTY
        if sys.stdin.isatty():
            ans = input(f"Apply these {n_changes} changes? [y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("aborted")
                sys.exit(0)

    # apply
    manifest_tmp = project_path / ".aris" / (MANIFEST_NAME + ".tmp")
    manifest_tmp.parent.mkdir(parents=True, exist_ok=True)
    write_manifest_tmp(plan, manifest_tmp, repo, project_path)
    print("\nApplying:")
    apply_plan(plan, manifest_tmp, project_skills_dir, repo_skills_abs, dry_run=False, quiet=args.quiet)
    # commit manifest
    if manifest_path.is_file():
        prev = project_path / ".aris" / (MANIFEST_NAME + ".prev")
        try:
            shutil.copy2(manifest_path, prev)
        except Exception:
            pass
    manifest_tmp.replace(manifest_path)
    ensure_tools_symlink(project_path, repo, dry_run=False)
    ensure_agents(project_path, repo, dry_run=False)
    apply_zcode_skill_patches(project_path, dry_run=False)
    # AGENTS.md managed block (best-effort, like original CLAUDE.md)
    installed_names = [n for a, k, n, e in plan if a in ("REUSE", "ADOPT", "CREATE", "UPDATE_TARGET")]
    try:
        update_agents_md(project_path, repo, installed_names, dry_run=False, no_doc=args.no_doc)
    except Exception as e:
        warn(f"AGENTS.md update failed (best-effort, continuing): {e}")
    # declined + global pointer (best-effort after manifest)
    try:
        declined_path.parent.mkdir(parents=True, exist_ok=True)
        # latter: declined_candidates - selected
        final_declined = {d for d in declined_candidates if d not in selected}
        if final_declined or declined_path.is_file():
            tmp = declined_path.with_suffix(".tmp")
            tmp.write_text("\n".join(sorted(final_declined)) + ("\n" if final_declined else ""), encoding="utf-8")
            tmp.replace(declined_path)
    except Exception as e:
        warn(f"cannot write declined file: {e}")
    ensure_global_pointer(repo, dry_run=False)
    ensure_hooks(project_path, repo, dry_run=False, no_hooks=args.no_hooks)

    # verify
    bad = 0
    for kind, name, src, tgt, mode in load_manifest(manifest_path):
        vtarget = project_path / tgt
        if not is_symlink(vtarget):
            # On Windows junction, is_symlink returns True for junction; also check exists
            if not vtarget.exists():
                warn(f"verify: {vtarget} missing")
                bad += 1
    if bad == 0:
        print(f"\n✓ Install complete. {n_changes} changes applied.")
    else:
        print(f"\n⚠ Install finished with {bad} missing links — check warnings above.")


if __name__ == "__main__":
    main()
