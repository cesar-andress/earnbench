# Phase C — Agent patch collection

Phase C collects **patch attempts** from multiple agent arms on the same
validated instance set prepared from a Phase A run. It does **not** compute
Earned Fraction (EF); Phase D / existing EarnBench regrade computes EF later.

## Commands

```bash
earnbench phase-c prepare \
  --phase-a-run experiments/runs/phase_a_smoke \
  --output experiments/runs/phase_c_agents \
  --agent-arms configs/phase_c_arms.yaml \
  --instances experiments/runs/phase_a_smoke/retained_instances.json

earnbench phase-c run \
  --manifest experiments/runs/phase_c_agents/run_manifest.json \
  --output experiments/runs/phase_c_agents \
  --workers 4 \
  --resume

earnbench phase-c summarize \
  --run experiments/runs/phase_c_agents
```

## Inputs

| Flag | Command | Required | Description |
|------|---------|----------|-------------|
| `--phase-a-run` | prepare | yes | Completed Phase A directory (`run_manifest.json`, `summary.csv`) |
| `--output` | prepare, run | yes* | Phase C output root |
| `--agent-arms` | prepare | yes | YAML file listing agent arms |
| `--instances` | prepare | no | CSV or JSON instance-id list; default: retained rows from Phase A `summary.csv` |
| `--manifest` | run | yes | Prepared `run_manifest.json` |
| `--workers` | run | no | Concurrent collection workers (default: 1) |
| `--resume` | run | no | Skip tasks already recorded in `attempts.jsonl` |
| `--run` | summarize | yes | Completed Phase C output directory |

\*For `run`, `--output` overrides the directory stored in the manifest when set.

## Ollama models

Pull local models before `phase-c run`:

```bash
ollama pull qwen3-coder:30b
ollama pull qwen2.5-coder:32b
ollama pull deepseek-coder-v2:lite
ollama pull devstral
```

## Agent arms (`arms.yaml`)

```yaml
arms:
  - id: ollama_qwen3_coder_30b
    provider: ollama
    model: qwen3-coder:30b
    replicates: 3
    temperature: 0.2
  - id: ollama_devstral
    provider: ollama
    model: devstral
    replicates: 3
    temperature: 0.2
  - id: claude_code
    provider: external_cli
    command: "claude-code --model ... --input {prompt_file} --output {output_file}"
    replicates: 3
```

Supported providers:

- **`ollama`** — local Ollama HTTP API (`/api/tags`, `/api/generate`). Checks model
  availability, records prompt hash, stores full JSON trajectory, extracts unified diff.
- **`external_cli`** — placeholder adapters for Claude Code, Codex, Gemini, etc.
  Command templates may use `{prompt_file}`, `{output_file}`, `{instance_id}`,
  `{replicate}`, `{seed}`, `{model}`, `{agent_id}`. Do not hardcode secrets in YAML.

Expected agent arm ids for the pilot:

- `claude_code`, `codex`, `gemini`
- `ollama_qwen3_coder_30b` (`qwen3-coder:30b`)
- `ollama_qwen25_coder_32b` (`qwen2.5-coder:32b`)
- `ollama_deepseek_coder_v2_lite` (`deepseek-coder-v2:lite`)
- `ollama_devstral` (`devstral`)

## Prompt generation

Each attempt uses a deterministic SWE-bench repair prompt built from metadata:

- `instance_id`, `problem_statement`, `repo`, `base_commit`
- Instructions to output **only** a unified diff

The prompt excludes `test_patch`, held-out information, and EarnBench perturbation details.

## Attempt record schema

Each line in `attempts.jsonl` contains:

| Field | Description |
|-------|-------------|
| `agent` | Arm id from `arms.yaml` |
| `model` | Model name (Ollama or CLI) |
| `provider` | `ollama` or `external_cli` |
| `instance_id` | SWE-bench instance id |
| `replicate` | Replicate index (0-based) |
| `seed` | Deterministic episode seed |
| `scaffold_id` | `earnbench_phase_c_v1` |
| `prompt_sha256` | SHA-256 of rendered prompt |
| `patch_path` | Relative path under output root |
| `patch_sha256` | SHA-256 of extracted patch (empty if none) |
| `trajectory_log_ref` | Relative path to trajectory log |
| `status` | `ok`, `no_patch`, `error`, or `skipped` |
| `started_at_utc` / `completed_at_utc` | ISO timestamps |
| `error` | Error message when status is not `ok` |

## Output layout

```text
<output>/
├── run_manifest.json
├── attempts.jsonl
├── attempts.csv
├── failures.csv
├── summary.json                 # from `earnbench phase-c summarize`
├── prompts/<agent>/<instance_id>/replicate_<k>.txt
├── patches/<agent>/<instance_id>/replicate_<k>.patch
└── trajectories/<agent>/<instance_id>/replicate_<k>.log
```

## Resume

With `--resume`, a task is skipped when its key
(`agent:instance_id:r<replicate>`) already appears in `attempts.jsonl`.

## Relationship to EF

Phase C is collection-only. Downstream Phase D regrade (or existing EarnBench
grading commands) consumes collected patches and computes EF@Π using frozen
EarnBench semantics. Phase C must not change EF, Π, invalid semantics, Phase A,
or Phase B behavior.
