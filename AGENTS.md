# Codex working instructions

## Model and reasoning preferences

- Reserve GPT-6 Astra for design or major reasoning: substantive UI/UX design,
  architecture decisions, difficult root-cause analysis, or complex tradeoffs.
- Prefer GPT-6 Luna for routine edits and GPT-6 Sol for substantial implementation,
  when available. Routine styling or implementing an agreed design does not by
  itself justify Astra. Return to a smaller model after the design/reasoning phase.
- Prefer low reasoning for simple work, medium for ordinary implementation, and
  higher effort only when the problem needs it. Prefer Standard speed.
- Apply these preferences through supported model controls when available.
  Markdown does not switch the active model. If a switch is needed but unavailable,
  state that once and identify the model the user should select; never claim a switch.

## Efficient execution

- Make the smallest complete change that meets the request; avoid unrelated cleanup.
- Search narrowly with `rg`; read relevant sections and expand only as needed.
  Reuse findings. Avoid whole-repository dumps, repeated reads, and large logs.
- Batch independent checks. Run relevant tests and required checks; repeat only
  after changes, failures, or new evidence. Do not sacrifice correctness for savings.
- Keep progress updates and final answers brief: outcome, validation, and blockers.
- Use one agent unless the user explicitly requests delegation. Load only relevant
  skills and tools. Keep persistent instructions short.
- Keep related follow-ups together. At a task boundary, offer a short handoff if
  context is crowded; a new chat does not reset weekly/session allowance.
- For usage optimization or a context handoff, use `$codex-efficiency` when available.
