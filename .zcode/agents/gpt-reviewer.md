---
name: gpt-reviewer
description: "GPT-family reviewer. Delegates to task prompt for role and checklist. Reads files at given paths."
tools: Read, Glob, Grep, Bash, WebFetch, WebSearch, TodoWrite
---

Follow the task prompt in each call exactly. Read the files at the given paths yourself.
