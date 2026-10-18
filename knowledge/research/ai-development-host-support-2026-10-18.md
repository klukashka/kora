# AI development host support

Checked: 2026-10-18. This record concerns contributor sessions, not supported versions of kora's product harness adapters.

## Official interfaces

- [Codex skills](https://learn.chatgpt.com/docs/build-skills) describes repository skill discovery and symlink support.
- [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) describes native delegation; available APIs depend on the host surface.
- [Claude Code skills](https://code.claude.com/docs/en/skills) describes `.claude/skills` discovery.
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents) distinguishes fresh subagents from inherited-conversation forks. A fresh child must explicitly read its assigned repository profile.

The canonical skill and relative Codex symlink share one instruction source. No custom agent definitions or global plugin changes are part of the setup. Official documentation establishes supported interfaces; the acceptance observations below establish what was actually exercised.

## Acceptance observations

Host acceptance is pending execution. No runtime compatibility result is claimed by the file layout alone.

## Enforcement limitations

On 2026-10-18, the repository's `master` protection required Markdown lint, Links and anchors, Mermaid diagrams and Example graph documents. The CI job GitHub templates was not a required merge status. Protection did not enforce administrators and did not require the branch to be up to date. This setup does not change repository administration; the PR reports the missing required status for owner/admin configuration.

Product checks remain pending component implementation. Live host acceptance uses model services as an operator setup check; it is not part of deterministic product CI. No host sample proves automatic routing for every future task or sandbox isolation from a reviewer prompt.
