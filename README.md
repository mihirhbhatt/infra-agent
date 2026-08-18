# Infra Agent (Infra-as-Prompt)

`infra-agent` is a prompt-driven infrastructure manager that can route intent (from issue comments, alerts, or schedules) into Terraform plan/apply workflows.

## Structure

```
infra-agent/
├── .github/
│   ├── workflows/
│   └── agent/
├── modules/
├── environments/
├── scripts/
├── policies/
├── README.md
└── LICENSE
```

## What this starter does

- Accepts predefined module commands such as `deploy network to dev`
- Supports freeform LLM-backed Terraform generation for AWS requests
- Generates a Terraform plan by default
- Applies only when `--apply` is explicitly passed
- Runs IaC guardrails with Checkov and Trivy when they are installed
- Stores lightweight run memory in local JSON

## Example local usage

```bash
python scripts/agent_loop.py --prompt "deploy network to dev"
python scripts/agent_loop.py --prompt "deploy compute to dev apply" --apply
python scripts/agent_loop.py --prompt "environment: dev
Create an S3 bucket for application logs with versioning enabled" --strategy llm
```

For EC2 PoC, `deploy compute ...` provisions one Amazon Linux 2023 instance (`t3.micro` by default).

## Command formats

```
deploy <module> to <environment> [apply]
destroy <environment> resources
```

Examples:
- `deploy network to dev`
- `deploy compute to prod apply`
- `destroy dev resources`
- `environment: staging` + freeform request with `--strategy llm`

## Hybrid execution model

- **Predefined modules** are used for strict commands like `deploy compute to dev`.
- **LLM generation** is used for freeform requests when you pass `--strategy llm` or use `--strategy auto` with a non-strict prompt.
- LLM generation requires `OPENAI_API_KEY` and optionally `OPENAI_MODEL`.

## GitHub issue workflow

- Open an issue with the infrastructure request using the built-in template in [.github/ISSUE_TEMPLATE/](/C:/Users/mihir/OneDrive/Documents/iap.worktrees/github-aws-environment-variables/.github/ISSUE_TEMPLATE).
- On issue open/edit, the workflow plans the request.
- Comment `/infra apply` to apply the issue request.
- Comment `/infra plan` to re-run plan only.
- Comment `/infra destroy dev resources` to destroy predefined module resources in an environment.

## Guardrails

- **Checkov** scans Terraform/IaC misconfigurations.
- **Trivy config** scans the generated Terraform before apply.
- For this repo, these are the most relevant controls. Traditional DAST is only meaningful after deploying an application endpoint, not just infrastructure.

## Notes

- Terraform CLI must be installed for local runs.
- AWS credentials must be available in your execution environment (for example via `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and optional `AWS_SESSION_TOKEN`).
- For LLM-driven generation, set `OPENAI_API_KEY` in your local environment or GitHub Actions secrets.
- GitHub Actions workflows in `.github/workflows` provide issue-command, scheduled maintenance, alert remediation, and merged PR deployment entry points.
