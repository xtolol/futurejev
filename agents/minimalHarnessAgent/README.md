# MinimalHarnessAgent

`minimalHarnessAgent` lets Futuresim run an external coding-agent CLI as a
forecasting agent while Futuresim keeps ownership of dates, questions,
market snapshots, scoring, article visibility, search date caps, and prediction
submission.

`MinimalHarnessAgent(...)` is still the compatibility entrypoint. Direct
construction dispatches to the backend subclass selected by
`MinimalHarnessConfig.harness_backend`.

## Runtime Shape

On each `act()` call, the shared driver:

1. Updates the per-agent workspace and internal state.
2. Stages only date-visible articles into `workspace/articles/`.
3. Writes `state.json` for the MCP server.
4. Starts the host MCP relay and optional sandbox/egress proxy when configured.
5. Starts or resumes the selected CLI backend.
6. Waits for `submit_forecasts` / `next_day` signals written by the MCP server.
7. Returns Futuresim `PredictionSubmission` objects to the environment.

The CLI sees the workspace. Driver/MCP coordination files live in the internal
run directory and are not meant to be edited by the model.

## File Map

- `__init__.py`: exports the public classes for this package.
- `agent.py`: shared `MinimalHarnessAgent` driver. Owns lifecycle, workspace
  setup, backend dispatch, BaseAgent integration, shared process launching/log
  plumbing, signal waiting, cleanup, and backend hook definitions.
- `config.py`: `MinimalHarnessConfig` plus `DEFAULT_EGRESS_ALLOWLIST`.
  Contains backend selection, prompt mode, search/date controls, resume flags,
  sandbox flags, network isolation, warmup/static-search knobs, and bootstrap
  config.
- `state.py`: `StateHelpers`. Handles `state.json`, per-agent `market.csv`,
  bootstrap seeding, active-memory snapshot dates, embedding-server detection,
  and date-gated article staging.
- `mcp_helpers.py`: `McpHelpers`. Builds MCP command lines/config, filters enabled MCP
  tools by prompt mode, and manages the host-side MCP relay used by sandboxed
  harnesses.
- `mcp_server.py`: FastMCP server exposed to the CLI. Provides forecast tools
  such as `search_news`, `submit_forecasts`, `next_day`, and active-memory2
  memory tools. Heavy imports are lazy so the MCP handshake stays fast.
- `sandbox.py`: `SandboxHelpers`. Builds the `bwrap` filesystem sandbox and
  optional network-isolated egress bridge. Keeps repo data, datasets, raw search
  indices, sibling runs, and non-harness home dirs hidden from the model.
- `egress_proxy.py`: host-side allowlist proxy for `network_isolation=True`.
  Handles HTTP CONNECT/proxy traffic and raw-forwards the local embedding server.
- `claude_code_agent.py`: `ClaudeCodeAgent`, the Claude Code CLI backend.
- `codex_agent.py`: `CodexAgent`, the Codex CLI backend. Also owns Codex warmup
  and static-search session aggregation helpers.
- `opencode_agent.py`: `OpenCodeAgent`, the OpenCode CLI backend.
- `prompts/`: prompt builders, grouped away from harness runtime code.
- `prompts/prompt.py`: default system prompt builder and Codex warmup prompt
  builder.
- `prompts/prompt_no_memory.py`: system prompt builder for
  `prompt_mode="no_memory"`.
- `prompts/prompt_active_memory.py`: daily fresh-context prompt builder for
  `prompt_mode="active_memory"`.
- `prompts/prompt_active_memory2.py`: daily prompt and memory-update prompts
  for `prompt_mode="active_memory2"`.
- `static_search.py`: static-search cache helpers for the Codex warmup ablation.
- `README.md`: this folder guide.

## Backends

Set `harness_backend` in config:

```yaml
defaults:
  harness_backend: "codex"        # "claude_code", "opencode", or "codex"
  prompt_mode: "default"          # see prompt modes below
```

Backend subclasses own CLI-specific behavior:

- command construction and environment variables
- launch/resume/session-id handling
- backend-specific stdout/stderr log names
- install-tree and home-state sandbox binds
- whether the backend respawns each day

Shared Futuresim semantics should stay in `agent.py` or the helper modules,
not in one backend file, unless the behavior is truly backend-specific.

## Prompt Modes

- `default`: persistent-ish CLI workflow with the shared system prompt and MCP
  tools. Resume behavior depends on backend resume flags.
- `no_memory`: no persistent memory prompt variant. Useful for clean ablations.
- `active_memory`: fresh CLI session per day. The prompt asks the model to read
  prior memory files and write today's memory files before `next_day`.
- `active_memory2`: fresh CLI session per day, but memory is managed through MCP
  tools. The MCP server loads prior memory and saves today's memory on
  `next_day`.
- `warmup`: Codex-only, fresh session per question during explicit warmup.
- `static_search`: Codex-only warmup shape with one precomputed title-search
  result and only forecast submission enabled.

`active_memory` and `active_memory2` support Codex, Claude Code, and OpenCode.
`warmup` and `static_search` currently require Codex.

## Optional Jev evidence analysis

In sandboxed Codex runs with `prompt_mode: default`, set `enable_jev_tool: true`
under `defaults` or on an individual agent to expose `analyze_evidence`. The
flag defaults to false. `jev_model` defaults to `jev-latest`. Set
`TYPESAFE_API_KEY` in the host environment or the repo-root `.env` for live
calls. The driver removes this key from the CLI environment.

Codex reads `market.csv` and decides whether a passage warrants analysis.
`analyze_evidence(question_id, evidence_text, source_id="")` looks up the
active question and simulation date in host state. It passes that context and
the excerpt to `TypeSafeEvidenceClient.analyze()`, which sends one
`system_one()` request. Jev returns a relevance score from 0 to 3, a direction
classification, and the probability that the passage explicitly confirms the
event. Codex weighs those judgments, chooses its own event probabilities, and
calls `submit_forecasts()` and `next_day()`. A failed analysis returns a
structured error so Codex can finish its turn.

### Running the Atlas comparison

From the repo root in WSL or Linux, use a signed-in Codex CLI, `bwrap`, and
`socat`. Both smoke YAMLs point to `/usr/local/bin/codex` and the existing
synthetic `atlas_001` dataset at `../experiments/smoke/questions.jsonl`.
If your files live elsewhere, update the same paths in both YAMLs. Both runs
use exact answer matching, disabled news search, and network isolation.

```bash
uv run --locked python scripts/run_forecast_sim.py --config configs/minimalHarness/llm_direct_smoke.yaml --output_base "$PWD/logs/current_sim"
uv run --locked python scripts/run_forecast_sim.py --config configs/minimalHarness/llm_jev_tool_smoke.yaml --output_base "$PWD/logs/current_sim"
```

The tool-enabled run lets Codex choose whether to consult Jev. Under
`logs/current_sim/<sim_name>/<timestamp>/`, inspect the agent's
`codex_stdout.jsonl` for tool invocations and `agents/<agent_id>/jev_calls.jsonl`
for Jev requests. Each logged analysis includes the simulation date, question ID,
evidence hash, latency, and status. Successful calls also record the returned
model, signals, and available token counts. The run's `actions.jsonl` contains
the submitted distribution and resolution, and `daily_metrics.csv` records
the scores. The direct baseline can run without a TypeSafe key.

### Checking the integration

The standalone check sends one synthetic passage to Jev and prints its three
normalized signals. The test suite exercises the adapter and the MCP server.
Set the key to run the live cases or leave it unset to skip them.

```bash
uv run --locked python scripts/check_jev_evidence.py
uv run --locked --env-file .env python -m unittest discover -s tests -v
```

These configs exercise synthetic evidence supplied by the agent. Historical
benchmarks require host-verified article content and publication dates to enforce
the simulation cutoff. The optional `source_id` is a caller-supplied label.

## Sandbox And Data Visibility

With `sandbox=True`, the CLI runs under `bwrap`. The model sees only the
workspace, internal coordination dir, harness runtime state, required OS files,
and the MCP/egress socket dirs. The Futuresim repo, datasets, raw search
indices, sibling simulation outputs, and most of `/home` remain hidden.

Search runs through MCP. In sandboxed mode the MCP server stays host-side, so
the CLI cannot open LanceDB/search files directly or bypass the date cap.
Articles are exposed incrementally in `workspace/articles/`; sandboxed runs use
hardlinks plus a read-only bind so `articles_base` stays hidden and source files
cannot be modified through the sandbox.

With `network_isolation=True`, `bwrap` uses a private network namespace and the
CLI reaches only configured egress targets through the host allowlist proxy.
The local embedding server is exposed separately through a raw socket forward.

## Adding Or Changing Code

Use the smallest module that owns the behavior:

- shared simulation lifecycle: `agent.py`
- state/data visibility: `state.py`
- MCP command/relay behavior: `mcp_helpers.py`
- filesystem/network isolation: `sandbox.py`
- backend launch/resume quirks: the backend file
- prompt wording: the matching `prompts/prompt*.py` file
- config surface: `config.py`

Backend files should implement the existing hook surface instead of adding
backend branches to the shared driver:

- `_prepare_harness_launch`
- `_start_harness`
- `_resume_harness`
- `_respawns_each_day`
- `_next_day_returns_immediately`
- `_after_next_day_signal`
- `_add_sandbox_harness_install_binds`
- `_sandbox_harness_home_subdirs`

After edits, at minimum run:

```bash
python -m py_compile agents/minimalHarnessAgent/*.py
```
