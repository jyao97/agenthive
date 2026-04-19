# CLAUDE.md
> Read this file at the start of every task. Rarely modified — only update when project structure or conventions change.

## Universal Rules
- Think from first principles. Don't assume the user knows exactly what they want or the best way to get it. Start from the original requirement, question the approach, and suggest a better path if one exists
- Think step by step. Investigate before coding — read relevant code, trace the full flow, print findings before proposing a fix
- When a task is complex, break it into sub-tasks and spawn sub-agents to work in parallel
- Never guess. If unsure, read the code, check logs, or run a test first
- Every task must produce a visual verification artifact (screenshot, plot, diff, rendered output)
- If the goal or motivation is unclear, stop and discuss before writing code. If the goal is clear but the path isn't optimal, say so and suggest the better approach

## Do NOT
- Do not refactor or rename files unless the task explicitly requires it
- Do not delete or modify tests unless asked
- Do not change dependencies/package versions without explicit approval

## Output Rules
- Keep responses concise — no long explanations unless asked
- For large outputs (logs, data), write to a file instead of printing to stdout
- Truncate error logs to the relevant section, don't paste entire stack traces

## Git Conventions
- Commit message format: `[scope] brief description` (e.g. `[frontend] fix image zoom gesture`)
- Commit frequently — small atomic commits, not one giant commit at the end
- Commit to master directly when appropriate

## Release Conventions
- Tag format: `v<major>.<minor>.<patch>`
- Release notes: overview paragraph + categorized changelists with context
- Tone: factual changelog style — describe what changed, no adjectives or selling language
- Use `gh release create` with `--notes` — include `Full Changelog` compare link at the bottom
- Only create releases when explicitly asked by the user

## Commit Safety (public repo)
- **No secrets**: never commit API keys, tokens, passwords, or private keys — even as "examples". Use empty values in `.env.example` (e.g. `OPENAI_API_KEY=`), not fake-looking placeholder strings
- **No certificates**: `.pem`, `.crt`, `.key`, `.p12` etc. are gitignored — never force-add them
- **No personal paths**: use `~/`, `/home/YOUR_USERNAME/`, or relative paths in committed files — never hardcode real user home paths
- **No internal docs**: don't commit audit reports, release strategy, or internal planning docs — they expose security details and roadmap
- **No database files**: `.db`, `.sqlite3`, `:memory:` stubs are gitignored — never force-add them
- **Before committing new config/example files**: verify they contain only placeholders or empty values

## Concurrency Rules
- Check which files other agents are currently modifying before editing shared files
- Prefer creating new files over modifying existing shared ones when possible

## Code Style
- Follow existing patterns in the codebase — don't introduce new conventions
- Match the indentation, naming, and structure of surrounding code

## Project: cc-orchestrator (Xylocopa, formerly AgentHive)
- Tech Stack: Python 3.11+ (FastAPI), React (Vite), SQLite
- Top Dirs: certs/, frontend/, orchestrator/, project-configs/, projects/
- Config: .env
- Entry: orchestrator/main.py
- Tests: test_multi_question.py
- Build (frontend): `cd frontend && npx vite build && pm2 reload xylocopa-frontend` — required after any frontend edit (prod-build mode, no HMR)
- Test (frontend): `cd frontend && npx vitest run`
- Verify backend: `cd orchestrator && python3 -c "from models import *; print('OK')"`
- Restart: `./run.sh` or POST `/api/system/restart`
- Logs: `logs/server.log`, `logs/orchestrator.log`

## Project-Specific Rules
See README.md for detailed project documentation.
- Worktree sessions: always use `_resolve_session_jsonl()`, never bare `session_source_dir()`
- tmux pane matching: `xy-{agent_id[:8]}` (legacy `ah-{agent_id[:8]}` still recognized) session name is authoritative
- CWD matching: use `startswith(proj + "/")` not `==` (worktree subdirs)
- SQLAlchemy: `metadata` is reserved — use alt attr name with explicit column
- When fixing a helper, grep ALL call sites — don't assume you found them all
- Queued messages use stop-hook dispatch: PENDING in DB → stop hook fires → send via tmux → UserPromptSubmit confirms delivery
