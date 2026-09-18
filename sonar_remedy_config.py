"""First-run setup wizard and config store for the SonarRemedy pipeline.

Gathers Sonar, repository, worktree and provider settings; validates them;
persists non-secret configuration to a JSON file and keeps secrets (the Sonar
token and repository PAT) in environment variables only. The stored config
references the environment-variable NAMES, never the secret values.
"""

import argparse
import getpass
import json
import os
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

CONFIG_VERSION = 1
PROVIDERS = ("manual", "opencode", "codex", "claude", "copilot")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SONAR_TOKEN_ENV = "SONAR_TOKEN"
REPO_PAT_ENV = "GIT_PAT"


class ConfigError(ValueError):
    """Raised for invalid or unsafe SonarRemedy configuration."""


def normalize_sonar_url(raw: str) -> str:
    """Return the API base URL for a base or dashboard Sonar URL.

    Rejects empty values, non-http(s) schemes and URLs that embed a token
    (``sqa_*`` or a ``token=`` query parameter). A dashboard URL carrying
    ``?id=KEY`` reduces to scheme://host:port; other query/fragment content
    is stripped, preserving any reverse-proxy path.
    """
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        raise ConfigError("sonar.url must not be empty")
    if re.search(r"sqa_[A-Za-z0-9]+", raw) or re.search(r"[?&]token=", raw):
        raise ConfigError("sonar.url must not contain an embedded token")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError("sonar.url must be an absolute http:// or https:// URL")
    if parts.query or parts.fragment:
        if re.search(r"(?:^|&)id=", parts.query):
            return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    return raw


def detect_project_key(url: str) -> str:
    """Extract the project key from a full URL (``?id=`` or last path segment)."""
    parts = urlsplit(url or "")
    if parts.query:
        match = re.search(r"(?:^|&)id=([^&]+)", parts.query)
        if match:
            return unquote(match.group(1))
    path = (parts.path or "").strip("/")
    if path:
        return path.split("/")[-1]
    return ""


def detect_branch(url: str) -> str:
    """Extract the branch from a full URL's ``?branch=`` query, if present."""
    parts = urlsplit(url or "")
    if parts.query:
        match = re.search(r"(?:^|&)branch=([^&]+)", parts.query)
        if match:
            return unquote(match.group(1))
    return ""


def validate(cfg: dict[str, Any]) -> list[str]:
    """Return a list of human-readable validation errors; empty means valid."""
    if not isinstance(cfg, dict):
        return ["config must be an object"]
    errors = []
    if cfg.get("version") != CONFIG_VERSION:
        errors.append(f"unsupported config version (expected {CONFIG_VERSION})")

    sonar = cfg.get("sonar")
    if not isinstance(sonar, dict):
        errors.append("sonar must be an object")
    else:
        url = sonar.get("url", "")
        if not url:
            errors.append("sonar.url is required")
        else:
            try:
                normalize_sonar_url(url)
            except ConfigError as error:
                errors.append(str(error))
        if not sonar.get("project_key"):
            errors.append("sonar.project_key is required")
        if not ENV_NAME_RE.match(sonar.get("token_env", "")):
            errors.append("sonar.token_env must be an environment variable name")
        if "allow_http" in sonar and type(sonar["allow_http"]) is not bool:
            errors.append("sonar.allow_http must be a boolean")

    repo = cfg.get("repository")
    if not isinstance(repo, dict):
        errors.append("repository must be an object")
    else:
        if not repo.get("url"):
            errors.append("repository.url is required")
        if not ENV_NAME_RE.match(repo.get("pat_env", "")):
            errors.append("repository.pat_env must be an environment variable name")
        if not repo.get("main_branch"):
            errors.append("repository.main_branch is required")
        local_path = repo.get("local_path")
        if local_path is not None and (not isinstance(local_path, str) or not local_path):
            errors.append("repository.local_path must be a non-empty string when present")
        branches = repo.get("propagation_branches", [])
        if not isinstance(branches, list) or not all(isinstance(b, str) for b in branches):
            errors.append("repository.propagation_branches must be a list of strings")

    worktrees = cfg.get("worktrees")
    if not isinstance(worktrees, dict) or not worktrees.get("root"):
        errors.append("worktrees.root is required")

    if cfg.get("provider") not in PROVIDERS:
        errors.append("provider must be one of: " + ", ".join(PROVIDERS))
    return errors


def save(cfg: dict[str, Any], path: str) -> None:
    """Validate and atomically write the config to ``path``."""
    errors = validate(cfg)
    if errors:
        raise ConfigError("; ".join(errors))
    payload = json.dumps(cfg, indent=2, sort_keys=True, ensure_ascii=True)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.replace(tmp, path)


def load(path: str) -> dict[str, Any]:
    """Read and validate a config file."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    errors = validate(data)
    if errors:
        raise ConfigError("; ".join(errors))
    return data


def default_config_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".sonar-remedy", "config.json")


PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def projects_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".sonar-remedy", "projects")


def project_path(name: str) -> str:
    if not PROJECT_NAME_RE.match(name):
        raise ConfigError("project name must match [A-Za-z0-9_.-]{1,64}")
    return os.path.join(projects_dir(), name + ".json")


def save_project(name: str, cfg: dict[str, Any]) -> None:
    save(cfg, project_path(name))


def load_project(name: str) -> dict[str, Any]:
    path = project_path(name)
    if not os.path.isfile(path):
        raise ConfigError("project not found: " + name)
    return load(path)


def list_projects() -> list[str]:
    directory = projects_dir()
    if not os.path.isdir(directory):
        return []
    return sorted(name[:-5] for name in os.listdir(directory) if name.endswith(".json"))


def prompt(
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
) -> dict[str, Any]:
    """Interactively gather a validated config; secrets go to env, never the dict."""
    url_raw = input_fn("SonarQube URL (base, or full URL with project key): ")
    url = normalize_sonar_url(url_raw)
    project_key = detect_project_key(url_raw)
    if not project_key:
        project_key = input_fn("SonarQube project key: ").strip()
    if not project_key:
        raise ConfigError("project_key must not be empty")

    token = secret_fn("SonarQube token (masked): ")
    if not token and os.environ.get(SONAR_TOKEN_ENV):
        token = os.environ[SONAR_TOKEN_ENV]
    if not token:
        raise ConfigError("Sonar token must not be empty")
    os.environ[SONAR_TOKEN_ENV] = token

    allow_http = False
    if url.startswith("http://"):
        answer = input_fn("Allow HTTP (insecure; on-prem only) [no]: ").strip().lower()
        allow_http = answer in ("y", "yes", "si", "sí", "true", "1")

    repo_url = input_fn("Repository URL: ").strip()
    if not repo_url:
        raise ConfigError("repository URL must not be empty")

    pat = secret_fn("Repository PAT (masked): ")
    if not pat and os.environ.get(REPO_PAT_ENV):
        pat = os.environ[REPO_PAT_ENV]
    if not pat:
        raise ConfigError("repository PAT must not be empty")
    os.environ[REPO_PAT_ENV] = pat

    local_path = input_fn("Local repo path (main checkout, optional): ").strip()

    main_branch = input_fn("Main branch [main]: ").strip() or "main"

    branches_raw = input_fn("Additional propagation branches (comma-separated, optional): ").strip()
    propagation = [b.strip() for b in branches_raw.split(",") if b.strip()]

    worktree_root = input_fn("Worktree root directory: ").strip()
    if not worktree_root:
        raise ConfigError("worktree root must not be empty")

    provider = (
        input_fn("Provider (manual/opencode/codex/claude/copilot) [manual]: ").strip() or "manual"
    )
    if provider not in PROVIDERS:
        raise ConfigError("provider must be one of: " + ", ".join(PROVIDERS))

    repository = {
        "url": repo_url,
        "pat_env": REPO_PAT_ENV,
        "main_branch": main_branch,
        "propagation_branches": propagation,
    }
    if local_path:
        repository["local_path"] = local_path

    sonar = {"url": url, "project_key": project_key, "token_env": SONAR_TOKEN_ENV}
    if allow_http:
        sonar["allow_http"] = True

    return {
        "version": CONFIG_VERSION,
        "sonar": sonar,
        "repository": repository,
        "worktrees": {"root": worktree_root},
        "provider": provider,
    }


def persist_user_env(cfg: dict[str, Any]) -> None:
    """Best-effort: persist the referenced secrets to user-level env (Windows)."""
    try:
        import winreg
    except ImportError:
        raise ConfigError("user-level persistence is only supported on Windows") from None
    for env_name in (cfg["sonar"]["token_env"], cfg["repository"]["pat_env"]):
        value = os.environ.get(env_name)
        if not value:
            raise ConfigError(f"secret for {env_name} is not present in the environment")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, env_name, 0, winreg.REG_SZ, value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=default_config_path())
    parser.add_argument("--project", help="save under a project name in ~/.sonar-remedy/projects/")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="persist secrets to user-level environment variables (Windows)",
    )
    args = parser.parse_args(argv)
    try:
        cfg = prompt()
        if args.project:
            save_project(args.project, cfg)
            target = project_path(args.project)
        else:
            save(cfg, args.config)
            target = os.path.abspath(args.config)
        if args.persist:
            persist_user_env(cfg)
        print(
            json.dumps(
                {
                    "status": "configured",
                    "config": target,
                    "sonar": {
                        "url": cfg["sonar"]["url"],
                        "project_key": cfg["sonar"]["project_key"],
                    },
                    "repository": {
                        "url": cfg["repository"]["url"],
                        "main_branch": cfg["repository"]["main_branch"],
                    },
                    "provider": cfg["provider"],
                    "sonar_token_env": cfg["sonar"]["token_env"],
                    "repo_pat_env": cfg["repository"]["pat_env"],
                },
                sort_keys=True,
            )
        )
        return 0
    except ConfigError as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}))
        return 2
    except (KeyboardInterrupt, EOFError):
        print(json.dumps({"status": "cancelled", "reason": "operator_cancelled"}))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
