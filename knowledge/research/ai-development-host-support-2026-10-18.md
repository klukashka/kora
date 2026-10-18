# AI development host support

Checked: 2026-10-18. This record concerns contributor sessions, not supported versions of kora's product harness adapters.

## Official interfaces

- [Codex skills](https://learn.chatgpt.com/docs/build-skills) describes repository skill discovery and symlink support.
- [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) describes native delegation; available APIs depend on the host surface.
- [Claude Code skills](https://code.claude.com/docs/en/skills) describes `.claude/skills` discovery.
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents) distinguishes fresh subagents from inherited-conversation forks. A fresh child must explicitly read its assigned repository profile.

The canonical skill and relative Codex symlink share one instruction source. No custom agent definitions or global plugin changes are part of the setup. Official documentation establishes supported interfaces; the acceptance observations below establish what was actually exercised.

## Acceptance observations

The instruction snapshot is commit `4d65cb87b6a0d58eb6da35c2ac3a785856d4ce9d`. Separate clean disposable clones added a one-line editorial fixture to `knowledge/README.md`, changing “engineers” to “contributors”. No product code or production data was involved. Requests asked to review the last commit using the repository's normal process, without naming the skill. They authorized local independent review, prohibited edits/publication and explicitly excluded CI/readiness claims.

| Host/surface | Configuration | Observed result |
|---|---|---|
| Codex CLI `0.162.1`, macOS, `codex exec` | User config ignored; plugins/hooks disabled for the session; native multi-agent enabled, maximum four threads requested; read-only sandbox; default selection resolved to `gpt-6.1-sol` | Passed discovery and fresh correctness dispatch. The startup skill catalog included `kora-verify` through `.agents/skills`; the parent read the canonical skill, spawned a child with `fork_turns: "none"`, and the child read `AGENTS.md`, `references/correctness.md` and the fixture diff. Both finished without edits. |
| Claude Code CLI `2.1.299`, macOS, print mode | Project settings only; empty strict MCP configuration; `dontAsk` with read/delegation tools allowed; no session persistence; no Chrome | Skill discovery passed: the startup event listed `kora-verify`. Execution was blocked before inference by expired OAuth that could not refresh. No child ran; profile loading and fresh dispatch remain unverified. `claude auth status` separately reported not logged in. |

The Codex acceptance was repeated with session persistence because its `--json` stream included waits but omitted the underlying spawn call. Inspection of the parent and child rollout records established the actual call and child reads; a final answer claiming an independent review was not accepted as proof by itself. The child's metadata identified the observed parent and a fresh spawn. Relevant redacted observations are:

```text
startup skill catalog: kora-verify -> repository skill path
parent read: .claude/skills/kora-verify/SKILL.md
parent call: collaboration.spawn_agent, fork_turns = "none"
child source: subagent/thread_spawn, depth = 1, forked_from_id = null
child read: AGENTS.md .claude/skills/kora-verify/references/correctness.md
child inspected: fixture HEAD^..HEAD and knowledge/README.md
review result: no findings; CI explicitly not run; readiness not claimed
```

The Codex configuration still exposed personal skills; one sample additionally read the installed `gstack-review` skill. The setup does not require or modify it. Read-only Git commands produced macOS cache-permission diagnostics but exited zero with the requested diff; those diagnostics are retained as an environment limitation, not hidden as clean execution. No application/browser diagnostic check was being run.

Claude's startup event reported only built-in plugins (`cc-plugin-agents-md`, `cc-plugin-telemetry`, `cc-plugin-plugin-authoring`) and no MCP servers. Its inference count was zero. Completing acceptance requires the operator to restore authentication and rerun the cold review, then inspect actual child/profile events. Discovery alone cannot satisfy the both-host dispatch acceptance criterion.

## Routing exercises

The Codex host also classified the following hypothetical requests using the installed instructions. These are tabletop classifications, not executed product tests or simulated proof that those components exist.

| Case | Observed classification |
|---|---|
| Editorial wording fixture | Actual independent correctness review; other specialists not applicable; CI explicitly excluded by the exercise |
| IR/schema contract | Correctness before and after; performance/security by complexity and hostile-input effects; schema/example checks now, runtime checks upon implementation |
| Executable backend without console | Correctness, separate backend performance and applicable security; parity/integration/both databases; browser pending runnable console |
| Frontend behavior with runnable shell | Correctness, browser and separate performance; static/build, common smoke plus changed journeys, negative cases and diagnostics |
| Authentication | Correctness/security before and after; applicable browser/performance; real denied-access integration and login/denial journeys |
| Specification-only secrets boundary | Correctness/security design review and documentation checks; unimplemented runtime checks pending |
| Third-party provider installation | Correctness/security, installed fixture discovery/metadata/configuration/listing/external conformance; protection claims need real tests |
| Renamed/deleted files and untracked tests | Both rename paths/deletions/nonignored untracked content; semantic component inheritance; final committed evidence required |
| Unmapped implementation | Block until assigned component, contracts, reviews and checks |
| Change after passed review | Recompute scope, invalidate affected conclusions, repeat affected review/checks and final-revision CI |
| Missing independent delegation | Required review blocked; self-review cannot substitute |

Attempted negative exercises using `--disable multi_agent` and, separately, `agents.max_threads=1` did not prevent native delegation on this host: a review still ran in each. These configuration attempts are not evidence of an unavailable tool or enforced concurrency limits. The unavailable-tool classification above remains tabletop evidence. The skill's concurrency cap is a coordinator obligation, not a demonstrated host setting guarantee.

Current repository checks are Markdown lint, local links/anchors, Mermaid parsing, IR example validation and GitHub-template schemas. Their final-revision run links belong in the setup PR. Neither host smoke was a product CI run, and no application checks are marked passing.

## Enforcement limitations

On 2026-10-18, the repository's `master` protection required Markdown lint, Links and anchors, Mermaid diagrams and Example graph documents. The CI job GitHub templates was not a required merge status. Protection did not enforce administrators and did not require the branch to be up to date. This setup does not change repository administration; the PR reports the missing required status for owner/admin configuration.

Product checks remain pending component implementation. Live host acceptance uses model services as an operator setup check; it is not part of deterministic product CI. No host sample proves automatic routing for every future task or sandbox isolation from a reviewer prompt.
