from __future__ import annotations

import argparse
import re
from pathlib import Path

from memory import MemoryEvent, now_utc_iso, save_event
from tools import terraform_apply, terraform_init, terraform_plan, terraform_validate


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_MODULES = {"network", "compute"}
ALLOWED_ENVS = {"dev", "prod"}
DEPLOY_RE = re.compile(
    r"^\s*deploy\s+(?P<module>[a-zA-Z0-9_-]+)\s+to\s+(?P<env>[a-zA-Z0-9_-]+)(?:\s+(?P<apply>apply))?\s*$",
    re.IGNORECASE,
)
DESTROY_RE = re.compile(
    r"^\s*destroy\s+(?P<env>[a-zA-Z0-9_-]+)\s+resources?\s*$",
    re.IGNORECASE,
)


def parse_prompt(prompt: str) -> tuple[str, str, bool, str]:
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
    
    raise ValueError("Invalid command. Expected: 'deploy <module> to <environment> [apply]' or 'destroy <environment> resources'")


def ensure_allowed(module: str, environment: str) -> None:
    if module not in ALLOWED_MODULES:
        raise ValueError(f"Module '{module}' is not allowed. Allowed: {sorted(ALLOWED_MODULES)}")
    if environment not in ALLOWED_ENVS:
        raise ValueError(
            f"Environment '{environment}' is not allowed. Allowed: {sorted(ALLOWED_ENVS)}"
        )


def write_environment_module(environment: str, module: str) -> Path:
    env_dir = ROOT / "environments" / environment
    env_dir.mkdir(parents=True, exist_ok=True)

    module_source = f"../../modules/{module}"
    backend_config = """
  backend "s3" {
    bucket         = "infra-agent-terraform-state"
    key            = "dev/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
""" if environment == "dev" else """
  backend "s3" {
    bucket         = "infra-agent-terraform-state"
    key            = "prod/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
"""

    if module == "compute":
        main_tf = f"""terraform {{
  required_version = ">= 1.5.0"

  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}{backend_config}
}}

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
    else:
        main_tf = f"""terraform {{
  required_version = ">= 1.5.0"
{backend_config}
}}

module "{module}" {{
  source      = "{module_source}"
  environment = "{environment}"
}}
"""
    (env_dir / "main.tf").write_text(main_tf, encoding="utf-8")
    return env_dir


def print_result(step: str, result_code: int, stdout: str, stderr: str) -> None:
    print(f"[{step}] exit={result_code}")
    if stdout:
        print(stdout)
    if stderr:
        print(stderr)


def run(prompt: str, apply_flag: bool) -> int:
    module, environment, apply_requested, operation = parse_prompt(prompt)
    
    if operation == "destroy":
        # Only validate environment for destroy
        if environment not in ALLOWED_ENVS:
            raise ValueError(
                f"Environment '{environment}' is not allowed. Allowed: {sorted(ALLOWED_ENVS)}"
            )
        return destroy_environment(environment, apply_flag)
    
    # For deploy operations, validate both module and environment
    ensure_allowed(module, environment)
    env_dir = write_environment_module(environment, module)

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
    print_result(
        "terraform validate",
        validate_res.returncode,
        validate_res.stdout,
        validate_res.stderr,
    )
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

    should_apply = apply_flag or apply_requested
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


def destroy_environment(environment: str, apply_flag: bool) -> int:
    """Destroy all resources for an environment"""
    from subprocess import run as subprocess_run
    
    env_dir = ROOT / "environments" / environment
    if not env_dir.exists():
        print(f"Environment directory not found: {env_dir}")
        return 1
    
    # Recreate all module configurations for this environment
    print(f"Recreating Terraform configuration for {environment}...")
    for module in ALLOWED_MODULES:
        write_environment_module(environment, module)
    
    init_res = terraform_init(env_dir)
    print_result("terraform init", init_res.returncode, init_res.stdout, init_res.stderr)
    if init_res.returncode != 0:
        return init_res.returncode
    
    # Run terraform destroy
    print(f"Destroying all resources in {environment}...")
    destroy_res = subprocess_run(
        ["terraform", "destroy", "-auto-approve"],
        cwd=env_dir,
        capture_output=True,
        text=True,
    )
    print_result("terraform destroy", destroy_res.returncode, destroy_res.stdout, destroy_res.stderr)
    
    status = "success" if destroy_res.returncode == 0 else "failed"
    save_event(
        MemoryEvent(
            timestamp=now_utc_iso(),
            prompt=f"destroy {environment} resources",
            module="all",
            environment=environment,
            mode="destroy",
            status=status,
            detail="terraform destroy completed",
        )
    )
    return destroy_res.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Prompt-driven infra agent loop")
    parser.add_argument("--prompt", required=True, help="Infra command prompt")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Enable apply mode (otherwise plan-only)",
    )
    args = parser.parse_args()
    return run(prompt=args.prompt, apply_flag=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
