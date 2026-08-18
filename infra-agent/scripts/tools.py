from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_cmd(cmd: list[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        cmd=cmd,
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )


def terraform_init(environment_dir: Path) -> CommandResult:
    return run_cmd(["terraform", "init", "-input=false", "-no-color"], cwd=environment_dir)


def terraform_validate(environment_dir: Path) -> CommandResult:
    return run_cmd(["terraform", "validate", "-no-color"], cwd=environment_dir)


def terraform_plan(environment_dir: Path) -> CommandResult:
    return run_cmd(
        ["terraform", "plan", "-input=false", "-no-color", "-out=tfplan"],
        cwd=environment_dir,
    )


def terraform_apply(environment_dir: Path) -> CommandResult:
    return run_cmd(
        ["terraform", "apply", "-input=false", "-no-color", "tfplan"],
        cwd=environment_dir,
    )

