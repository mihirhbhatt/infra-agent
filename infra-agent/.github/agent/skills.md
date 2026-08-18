# Agent Skills and Constraints

## Capabilities

- Parse infra deployment requests from text prompts
- Map approved module names to local Terraform module paths
- Execute Terraform `init`, `validate`, `plan`, and optional `apply`
- Record execution events in memory for auditability

## Constraints

- Only modules declared in `agent.yaml` are deployable
- Default mode is plan-only (no apply)
- `prod` should be protected by approval in GitHub environments/policies
- Do not execute arbitrary shell commands from user prompts

