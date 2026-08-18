from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib import error, request

from memory import MemoryEvent, now_utc_iso, save_event
from tools import (
    checkov_scan,
    command_available,
    terraform_apply,
    terraform_destroy,
    terraform_init,
    terraform_plan,
    terraform_validate,
    trivy_config_scan,
)


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_MODULES = ("compute", "network")
ALLOWED_ENVS = ("dev", "staging", "prod")
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEPLOY_RE = re.compile(
    r"^\s*deploy\s+(?P<module>[a-zA-Z0-9_-]+)\s+to\s+(?P<env>[a-zA-Z0-9_-]+)(?:\s+(?P<apply>apply))?\s*$",
    re.IGNORECASE,
)
DESTROY_RE = re.compile(
    r"^\s*destroy\s+(?P<env>[a-zA-Z0-9_-]+)\s+resources?\s*$",
    re.IGNORECASE,
)
ENVIRONMENT_RE = re.compile(
    r"(?:^|\b)(?:environment|env)\s*[:=]\s*(dev|staging|prod)\b|\b(dev|staging|prod)\b",
    re.IGNORECASE | re.MULTILINE,
)
ISSUE_ENV_SECTION_RE = re.compile(
    r"^###\s+Environment\s*\n(?P<value>.*?)(?=^###\s+|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
ISSUE_REQUEST_SECTION_RE = re.compile(
    r"^###\s+Infrastructure request\s*\n(?P<value>.*?)(?=^###\s+|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def parse_strict_prompt(prompt: str) -> tuple[str, str, bool, str] | None:
    deploy_match = DEPLOY_RE.match(prompt)
    if deploy_match:
        module = deploy_match.group("module").lower()
        environment = deploy_match.group("env").lower()
        apply_requested = bool(deploy_match.group("apply"))
        return module, environment, apply_requested, "deploy"

    destroy_match = DESTROY_RE.match(prompt)
    if destroy_match:
        environment = destroy_match.group("env").lower()
        return "all", environment, True, "destroy"

    return None


def ensure_allowed(module: str, environment: str) -> None:
    if module not in ALLOWED_MODULES:
        raise ValueError(f"Module '{module}' is not allowed. Allowed: {list(ALLOWED_MODULES)}")
    if environment not in ALLOWED_ENVS:
        raise ValueError(f"Environment '{environment}' is not allowed. Allowed: {list(ALLOWED_ENVS)}")


def ensure_environment_allowed(environment: str) -> None:
    if environment not in ALLOWED_ENVS:
        raise ValueError(f"Environment '{environment}' is not allowed. Allowed: {list(ALLOWED_ENVS)}")


def infer_environment(prompt: str, explicit_environment: str | None) -> str:
    if explicit_environment:
        environment = explicit_environment.lower()
        ensure_environment_allowed(environment)
        return environment

    match = ENVIRONMENT_RE.search(prompt)
    if not match:
        raise ValueError(
            "Could not determine environment for prompt-driven generation. "
            "Include 'environment: dev|staging|prod' in the prompt or pass --environment."
        )

    environment = next(group for group in match.groups() if group)
    environment = environment.lower()
    ensure_environment_allowed(environment)
    return environment


def normalize_prompt(prompt: str) -> str:
    request_match = ISSUE_REQUEST_SECTION_RE.search(prompt)
    if not request_match:
        return prompt.strip()

    request_text = request_match.group("value").strip()
    environment_match = ISSUE_ENV_SECTION_RE.search(prompt)
    if not environment_match:
        return request_text

    environment_text = environment_match.group("value").strip().lower()
    if re.search(r"\b(dev|staging|prod)\b", request_text, re.IGNORECASE):
        return request_text
    return f"environment: {environment_text}\n{request_text}"


def state_key(environment: str, scope: str) -> str:
    return f"{environment}/{scope}.tfstate"


def backend_config(environment: str, scope: str) -> str:
    return f"""  backend "s3" {{
    bucket = "infra-agent-terraform-state"
    key    = "{state_key(environment, scope)}"
    region = "us-east-1"
  }}
"""


def module_root_configuration(environment: str, module: str) -> str:
    module_source = f"../../modules/{module}"
    if module == "compute":
        return f"""terraform {{
  required_version = ">= 1.5.0"

  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
{backend_config(environment, module)}}}

variable "aws_region" {{
  type    = string
  default = "us-east-1"
}}

module "{module}" {{
  source        = "{module_source}"
  environment   = "{environment}"
  aws_region    = var.aws_region
  instance_name = "infra-agent-{environment}-ec2"
}}
"""

    return f"""terraform {{
  required_version = ">= 1.5.0"
{backend_config(environment, module)}}}

module "{module}" {{
  source      = "{module_source}"
  environment = "{environment}"
}}
"""


def sanitize_llm_hcl(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def call_openai_for_terraform(prompt: str, environment: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is required for LLM-driven Terraform generation. "
            "Use predefined commands or set the secret first."
        )

    model = os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    system_prompt = (
        "You generate Terraform HCL for AWS infrastructure. "
        "Return Terraform HCL only with no markdown fences. "
        "Do not emit terraform, backend, or provider blocks because they are managed externally. "
        "Use var.environment and var.aws_region where relevant. "
        "Prefer explicit resource tags including Name, Environment, and ManagedBy = \"infra-agent\". "
        "Generate only resources, data sources, locals, variables, and outputs needed by the request."
    )
    user_prompt = (
        f"Environment: {environment}\n"
        f"AWS region variable is available as var.aws_region.\n"
        f"Environment variable is available as var.environment.\n"
        f"Request:\n{prompt}"
    )
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
    ).encode("utf-8")
    http_request = request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(http_request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI request failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenAI response shape: {body}") from exc

    return sanitize_llm_hcl(content)


def llm_root_configuration(environment: str, prompt: str) -> str:
    generated_hcl = call_openai_for_terraform(prompt, environment)
    return f"""terraform {{
  required_version = ">= 1.5.0"

  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
{backend_config(environment, "llm")}}}

variable "aws_region" {{
  type    = string
  default = "us-east-1"
}}

variable "environment" {{
  type    = string
  default = "{environment}"
}}

provider "aws" {{
  region = var.aws_region
}}

{generated_hcl}
"""


def write_environment_root(environment: str, main_tf: str) -> Path:
    env_dir = ROOT / "environments" / environment
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "main.tf").write_text(main_tf, encoding="utf-8")
    return env_dir


def print_result(step: str, result_code: int, stdout: str, stderr: str) -> None:
    print(f"[{step}] exit={result_code}")
    if stdout:
        print(stdout)
    if stderr:
        print(stderr)


def guardrails_required() -> bool:
    return os.getenv("ENFORCE_GUARDRAILS", "").lower() in {"1", "true", "yes"}


def run_guardrails(environment_dir: Path) -> int:
    failures = 0
    required = guardrails_required()

    if command_available("checkov"):
        checkov_res = checkov_scan(environment_dir)
        print_result("checkov", checkov_res.returncode, checkov_res.stdout, checkov_res.stderr)
        if checkov_res.returncode != 0:
            failures += 1
    elif required:
        print("[checkov] exit=1")
        print("Guardrails are enforced but checkov is not installed.")
        failures += 1
    else:
        print("[checkov] skipped")

    if command_available("trivy"):
        trivy_res = trivy_config_scan(environment_dir)
        print_result("trivy config", trivy_res.returncode, trivy_res.stdout, trivy_res.stderr)
        if trivy_res.returncode != 0:
            failures += 1
    elif required:
        print("[trivy config] exit=1")
        print("Guardrails are enforced but trivy is not installed.")
        failures += 1
    else:
        print("[trivy config] skipped")

    return 1 if failures else 0


def execute_plan_apply(prompt: str, module: str, environment: str, env_dir: Path, should_apply: bool) -> int:
    init_res = terraform_init(env_dir)
    print_result("terraform init", init_res.returncode, init_res.stdout, init_res.stderr)
    if init_res.returncode != 0:
        save_event(
            MemoryEvent(
                timestamp=now_utc_iso(),
                prompt=prompt,
                module=module,
                environment=environment,
                mode="plan",
                status="failed",
                detail="terraform init failed",
            )
        )
        return init_res.returncode

    validate_res = terraform_validate(env_dir)
    print_result("terraform validate", validate_res.returncode, validate_res.stdout, validate_res.stderr)
    if validate_res.returncode != 0:
        save_event(
            MemoryEvent(
                timestamp=now_utc_iso(),
                prompt=prompt,
                module=module,
                environment=environment,
                mode="plan",
                status="failed",
                detail="terraform validate failed",
            )
        )
        return validate_res.returncode

    plan_res = terraform_plan(env_dir)
    print_result("terraform plan", plan_res.returncode, plan_res.stdout, plan_res.stderr)
    if plan_res.returncode != 0:
        save_event(
            MemoryEvent(
                timestamp=now_utc_iso(),
                prompt=prompt,
                module=module,
                environment=environment,
                mode="plan",
                status="failed",
                detail="terraform plan failed",
            )
        )
        return plan_res.returncode

    guardrail_res = run_guardrails(env_dir)
    if guardrail_res != 0:
        save_event(
            MemoryEvent(
                timestamp=now_utc_iso(),
                prompt=prompt,
                module=module,
                environment=environment,
                mode="plan",
                status="failed",
                detail="guardrail scan failed",
            )
        )
        return guardrail_res

    if should_apply:
        apply_res = terraform_apply(env_dir)
        print_result("terraform apply", apply_res.returncode, apply_res.stdout, apply_res.stderr)
        status = "success" if apply_res.returncode == 0 else "failed"
        save_event(
            MemoryEvent(
                timestamp=now_utc_iso(),
                prompt=prompt,
                module=module,
                environment=environment,
                mode="apply",
                status=status,
                detail="terraform apply completed",
            )
        )
        return apply_res.returncode

    save_event(
        MemoryEvent(
            timestamp=now_utc_iso(),
            prompt=prompt,
            module=module,
            environment=environment,
            mode="plan",
            status="success",
            detail="terraform plan completed",
        )
    )
    return 0


def destroy_environment(environment: str) -> int:
    print(f"Destroying predefined module resources in {environment}...")
    overall_returncode = 0

    for module in ALLOWED_MODULES:
        env_dir = write_environment_root(environment, module_root_configuration(environment, module))
        init_res = terraform_init(env_dir)
        print_result(f"terraform init ({module})", init_res.returncode, init_res.stdout, init_res.stderr)
        if init_res.returncode != 0:
            overall_returncode = init_res.returncode
            continue

        destroy_res = terraform_destroy(env_dir)
        print_result(
            f"terraform destroy ({module})",
            destroy_res.returncode,
            destroy_res.stdout,
            destroy_res.stderr,
        )
        if destroy_res.returncode != 0:
            overall_returncode = destroy_res.returncode

    save_event(
        MemoryEvent(
            timestamp=now_utc_iso(),
            prompt=f"destroy {environment} resources",
            module="predefined",
            environment=environment,
            mode="destroy",
            status="success" if overall_returncode == 0 else "failed",
            detail="terraform destroy completed for predefined module states",
        )
    )
    return overall_returncode


def run(prompt: str, apply_flag: bool, strategy: str, explicit_environment: str | None) -> int:
    prompt = normalize_prompt(prompt)
    strict_prompt = parse_strict_prompt(prompt)

    if strategy in {"auto", "module"} and strict_prompt is not None:
        module, environment, apply_requested, operation = strict_prompt
        if operation == "destroy":
            ensure_environment_allowed(environment)
            return destroy_environment(environment)

        ensure_allowed(module, environment)
        env_dir = write_environment_root(environment, module_root_configuration(environment, module))
        return execute_plan_apply(prompt, module, environment, env_dir, apply_flag or apply_requested)

    if strategy == "module":
        raise ValueError(
            "Module strategy requires a strict command such as "
            "'deploy compute to dev apply' or 'destroy dev resources'."
        )

    environment = infer_environment(prompt, explicit_environment)
    env_dir = write_environment_root(environment, llm_root_configuration(environment, prompt))
    return execute_plan_apply(prompt, "llm", environment, env_dir, apply_flag)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prompt-driven infra agent loop")
    parser.add_argument("--prompt", required=True, help="Infra command prompt or freeform request")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Enable apply mode (otherwise plan-only)",
    )
    parser.add_argument(
        "--strategy",
        choices=("auto", "module", "llm"),
        default="auto",
        help="Execution strategy. 'auto' uses predefined modules for strict commands and LLM generation for freeform prompts.",
    )
    parser.add_argument(
        "--environment",
        help="Explicit environment for freeform prompts when it cannot be inferred from the request.",
    )
    args = parser.parse_args()
    return run(
        prompt=args.prompt,
        apply_flag=args.apply,
        strategy=args.strategy,
        explicit_environment=args.environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
