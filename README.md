# Infra Agent (Infra-as-Prompt)

`infra-agent` is a prompt-driven infrastructure manager that routes intent — from GitHub issues, issue comments, manual workflow triggers, alerts, or schedules — into Terraform plan/apply workflows on AWS. It supports both **predefined Terraform modules** and **AI-generated Terraform** from natural language.

---

## Structure

```
infra-agent/
├── .github/
│   ├── workflows/
│   │   ├── issue-command.yml        # Issue + manual trigger (predefined + AI)
│   │   ├── deploy-changes.yml       # PR merge + manual deploy (predefined modules)
│   │   ├── destroy-resources.yml    # Manual destroy
│   │   ├── alert-remediation.yml    # CloudWatch alert-driven remediation
│   │   └── scheduled-maintenance.yml # Weekly drift check
│   └── ISSUE_TEMPLATE/
│       └── infra-request.yml        # Issue form for infra requests
├── modules/
│   ├── compute/                     # EC2 instance + VPC + subnet
│   └── network/                     # Network baseline placeholder
├── environments/
│   ├── dev/
│   └── prod/
├── scripts/
│   ├── agent_loop.py                # Main agent — routing, LLM, guardrails
│   ├── tools.py                     # Terraform + Checkov + Trivy wrappers
│   └── memory.py                    # Run event logging
├── policies/
├── README.md
└── LICENSE
```

---

## Hybrid execution model

The agent supports two modes and auto-selects between them:

| Mode | When used | Example prompt |
|------|-----------|----------------|
| **Predefined module** | Prompt matches strict format | `deploy compute to dev apply` |
| **AI-generated Terraform** | Freeform natural language | `Create an S3 bucket for logs with versioning` |

The `--strategy` flag controls this:
- `auto` _(default)_ — strict command → module, freeform → LLM
- `module` — strict commands only
- `llm` — always call OpenAI to generate Terraform

---

## Triggering infrastructure

### 1. GitHub Actions — manual trigger (Actions tab)

Go to **Actions** → **issue-command** → **Run workflow**

| Input | Description | Example |
|-------|-------------|---------|
| `prompt` | Command or freeform request | `deploy compute to dev apply` |
| `environment` | Target environment | `dev` |
| `strategy` | `auto` / `module` / `llm` | `llm` for AI-generated |
| `apply` | `true` to apply, `false` to plan only | `true` |

**Predefined module example:**
```
prompt:      deploy compute to dev apply
environment: dev
strategy:    module
apply:       true
```

**AI-generated example:**
```
prompt:      Create a private S3 bucket for application logs with versioning enabled
environment: dev
strategy:    llm
apply:       false
```

---

### 2. GitHub issue — automatic trigger

Open an issue using the **Infrastructure request** template.

Fill in:
- **Environment**: `dev`, `staging`, or `prod`
- **Infrastructure request**: predefined command or freeform description

On issue **open/edit/reopen** → workflow runs a **plan automatically**.

Then comment on the issue to drive the workflow:

| Comment | Action |
|---------|--------|
| `/infra plan` | Re-run plan |
| `/infra apply` | Apply the infrastructure |
| `/infra destroy dev resources` | Destroy predefined module resources in `dev` |

**Example issue body — predefined module:**
```
deploy compute to dev
```

**Example issue body — AI-generated:**
```
Environment: dev
Create an RDS PostgreSQL instance with automated backups and a private subnet.
```

---

### 3. PR merge trigger

Merge a PR that touches any file → **deploy-changes.yml** auto-deploys both `network` and `compute` modules to the target environment.

---

### 4. Local CLI

```bash
# Predefined module — plan only
python scripts/agent_loop.py --prompt "deploy network to dev"

# Predefined module — apply
python scripts/agent_loop.py --prompt "deploy compute to dev apply" --apply

# Destroy predefined module resources
python scripts/agent_loop.py --prompt "destroy dev resources"

# AI-generated — plan only (environment inferred from prompt)
python scripts/agent_loop.py \
  --strategy llm \
  --prompt "environment: dev
Create an S3 bucket for application logs with versioning enabled"

# AI-generated — apply with explicit environment flag
python scripts/agent_loop.py \
  --strategy llm \
  --environment dev \
  --apply \
  --prompt "Create a t3.micro EC2 instance with an IAM role for S3 read access"
```

---

## Command formats (predefined module mode)

```
deploy <module> to <environment> [apply]
destroy <environment> resources
```

| Command | Description |
|---------|-------------|
| `deploy network to dev` | Plan network module for dev |
| `deploy compute to dev apply` | Plan + apply compute module for dev |
| `deploy compute to prod apply` | Plan + apply compute module for prod |
| `destroy dev resources` | Destroy all predefined module resources in dev |

Available modules: `network`, `compute`
Available environments: `dev`, `staging`, `prod`

---

## Guardrails (SAST / IaC security)

Every plan/apply runs security checks before applying:

| Tool | What it checks |
|------|----------------|
| **Checkov** | IaC misconfigurations, CIS benchmarks, best practices |
| **Trivy config** | Terraform security vulnerabilities and misconfigurations |

- Both tools run automatically when installed (always installed in GitHub Actions).
- Set `ENFORCE_GUARDRAILS=true` to **block apply on failures** (enabled by default in workflows).
- DAST is not applicable here — this repo provisions infrastructure, not application endpoints.

---

## Required GitHub secrets and variables

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `AWS_ACCESS_KEY_ID` | Secret | ✅ Always | AWS IAM access key |
| `AWS_SECRET_ACCESS_KEY` | Secret | ✅ Always | AWS IAM secret key |
| `AWS_REGION` | Secret | ✅ Always | AWS region e.g. `us-east-1` |
| `OPENAI_API_KEY` | Secret | ✅ For AI mode | OpenAI API key for Terraform generation |
| `OPENAI_MODEL` | Variable | Optional | OpenAI model, defaults to `gpt-4.1-mini` |

---

## Terraform state

State is stored remotely in S3 per environment and module:

```
s3://infra-agent-terraform-state/
├── dev/compute.tfstate
├── dev/network.tfstate
├── dev/llm.tfstate      ← AI-generated resources
├── prod/compute.tfstate
└── prod/network.tfstate
```

Create the S3 bucket before first run:
```bash
aws s3 mb s3://infra-agent-terraform-state --region us-east-1
aws s3api put-bucket-versioning \
  --bucket infra-agent-terraform-state \
  --versioning-configuration Status=Enabled
```

---

## Required AWS IAM permissions

The `github-actions-user` IAM user needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ec2:*"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObjectVersion"
      ],
      "Resource": [
        "arn:aws:s3:::infra-agent-terraform-state",
        "arn:aws:s3:::infra-agent-terraform-state/*"
      ]
    }
  ]
}
```

---

## Notes

- Terraform CLI must be installed for local runs.
- AWS credentials must be set via environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`) for local runs.
- AI-generated Terraform is placed in `environments/<env>/main.tf` and uses its own S3 state key (`llm.tfstate`) so it does not conflict with predefined module state.
- Destroy only tears down predefined module state. AI-generated resources must be destroyed manually via `terraform destroy` in the environment directory for now.

