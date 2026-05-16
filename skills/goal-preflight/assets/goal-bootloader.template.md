Use $goal-main-orchestrator.

Prepared bundle:
- Bundle root: {bundle_path}
- Repository root: {repo_root}
- Manifest: {manifest_path}
- Main prompt: {main_prompt_path}

Read the manifest and main prompt first. Treat main.prompt.md as the runtime contract. Do not infer paths from the current working directory; use the bundle root and repository root above.

If the bundle root or repository root above is wrong because files moved, stop and regenerate the bootloader with goal-preflight. Do not hand-edit these paths.

Pass only absolute paths to goal orchestration scripts. If a script entry path would be relative or would contain `..` traversal, stop and regenerate the bundle or bootloader.

Mandatory bootstrap first: verify runtime skill availability before prompt audit. Resolve GOAL_SKILLS_ROOT from ${CODEX_HOME:-$HOME/.codex}/skills, falling back to $HOME/.agents/skills, then run check_goal_skill_availability.py for goal-main-orchestrator and goal-branch-orchestrator. If either skill or required script is unavailable, return blocked and ask the user to install the skills package.

Mandatory second action: create and run the prompt-audit packet over job.manifest.json, main.prompt.md, and every listed branch prompt. Do not create branch worktrees or launch branch orchestrators unless bootstrap passed and prompt-audit.json says status=pass, can_start=true, and pins the manifest and repository root above.

Parallelism is the default. Respect max_active_branch_agents from job.manifest.json; never exceed 4. Launch every branch in the current wave concurrently up to that limit. Run branch waves sequentially. Collect finished branch status/review artifacts and close finished branch orchestrator agents before launching replacements. A single-branch or otherwise serialized plan is valid only when job.manifest.json records a serial_reason or parallelization_rationale.

Finish only when main.prompt.md Definition of Done is falsifiably satisfied by status files, review files, command evidence, and final git state. If anything is missing or unverifiable, return blocked or partial, not pass.
