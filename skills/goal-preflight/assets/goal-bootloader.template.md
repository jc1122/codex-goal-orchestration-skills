Use $goal-main-orchestrator.

Prepared bundle:
- Manifest: {manifest_path}
- Main prompt: {main_prompt_path}

Read the manifest and main prompt first. Treat main.prompt.md as the runtime contract.

Mandatory bootstrap first: verify runtime skill availability before prompt audit. Resolve GOAL_SKILLS_ROOT from ${CODEX_HOME:-$HOME/.codex}/skills, falling back to $HOME/.agents/skills, then run check_goal_skill_availability.py for goal-main-orchestrator and goal-branch-orchestrator. If either skill or required script is unavailable, return blocked and ask the user to install the skills package.

Mandatory second action: create and run the prompt-audit packet over job.manifest.json, main.prompt.md, and every listed branch prompt. Do not create branch worktrees or launch branch orchestrators unless bootstrap passed and prompt-audit.json says status=pass and can_start=true.

Respect max_active_branch_agents=5. Run branch waves sequentially. Keep at most 5 branch orchestrator agents active. Collect finished branch status/review artifacts and close finished branch orchestrator agents before launching replacements.

Finish only when main.prompt.md Definition of Done is falsifiably satisfied by status files, review files, command evidence, and final git state. If anything is missing or unverifiable, return blocked or partial, not pass.
