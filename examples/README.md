# Example Configs

Two config files for the two entry points into HCAG:

| File | Loaded by | Placement |
|---|---|---|
| [`agent.toml`](./agent.toml) | Runtime `AgentRuntime` — the LLM that answers user turns | Anywhere; pass path explicitly: `load_agent_config(Path("./agent.toml"))` |
| [`kb/hcag.toml`](./kb/hcag.toml) | CLI `hcag preprocess` / `hcag aggregate` — build-time metadata generation | Must sit at the **root of the KB directory**; auto-discovered |

## Two configs, two models

The build-time LLM (CLI) and runtime LLM (agent) are separately configured on purpose. Common pattern:

- **CLI:** cheap+fast (e.g., Haiku) — metadata generation runs once per rebuild.
- **Runtime:** stronger (e.g., Sonnet) — quality matters for every turn.

## Credentials

Never put API keys in TOML files. Set env vars instead:

```bash
# Anthropic direct
export ANTHROPIC_API_KEY=sk-ant-...

# AWS Bedrock (also picks up ~/.aws/credentials and IAM roles)
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-east-1

# Ollama (only if not on default localhost:11434)
export OLLAMA_API_BASE=http://your-ollama-host:11434
```

## Copy-paste quickstart

```bash
# 1. Copy the CLI config into your KB
mkdir -p ./my-kb
cp examples/kb/hcag.toml ./my-kb/hcag.toml

# 2. Copy the agent config wherever you launch the runtime from
cp examples/agent.toml ./agent.toml

# 3. Point kb_root in agent.toml at ./my-kb
```
