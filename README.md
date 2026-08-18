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

- Accepts infra commands such as: `deploy network to dev`
- Generates a Terraform plan by default
- Applies only when `--apply` is explicitly passed
- Stores lightweight run memory in local JSON

## Example local usage

```bash
python scripts/agent_loop.py --prompt "deploy network to dev"
python scripts/agent_loop.py --prompt "deploy compute to dev apply" --apply
```

For EC2 PoC, `deploy compute ...` provisions one Amazon Linux 2023 instance (`t3.micro` by default).

## Command format

```
deploy <module> to <environment> [apply]
```

Examples:
- `deploy network to dev`
- `deploy compute to prod apply`

## Notes

- Terraform CLI must be installed for local runs.
- AWS credentials must be available in your execution environment (for example via `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and optional `AWS_SESSION_TOKEN`).
- GitHub Actions workflows in `.github/workflows` provide issue-command, scheduled maintenance, alert remediation, and merged PR deployment entry points.
