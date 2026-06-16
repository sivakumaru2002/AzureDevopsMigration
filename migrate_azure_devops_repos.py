import argparse
import base64
import json
import os
import stat
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request


GIT = shutil.which("git.exe") or shutil.which("git")
API_VERSION = "7.1-preview.1"
PROCESS_API_VERSION = "6.0"
PROJECTS_API_VERSION = "6.0"
ENV_FILE = ".env"


def fail(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)


def build_basic_auth_header(pat: str) -> str:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def resolve_env_file_from_argv(argv: list[str]) -> Path:
    for index, argument in enumerate(argv):
        if argument.startswith("--env-file="):
            return Path(argument.split("=", 1)[1])

        if argument == "--env-file" and index + 1 < len(argv):
            return Path(argv[index + 1])

    return Path(ENV_FILE)


def remove_readonly_and_retry(func, path: str, _exc_info) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def safe_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onerror=remove_readonly_and_retry)


class AzureDevOpsClient:
    def __init__(self, organization: str, pat: str, base_url: str):
        self.organization = organization
        self.base_url = base_url.rstrip("/")
        self.authorization = build_basic_auth_header(pat)

    def _request(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        query_string = parse.urlencode(query or {})
        url = f"{self.base_url}/{parse.quote(self.organization, safe='')}" + path
        if query_string:
            url = f"{url}?{query_string}"

        return self._request_url(method, url, body=body)

    def _request_url(self, method: str, url: str, body: dict | None = None):
        data = None
        headers = {
            "Authorization": self.authorization,
            "Accept": "application/json",
        }

        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        http_request = request.Request(url, data=data, headers=headers, method=method)

        try:
            with request.urlopen(http_request) as response:
                payload = response.read().decode("utf-8")
                parsed_body = json.loads(payload) if payload else {}
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                return parsed_body, response_headers
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {url} failed with {exc.code}: {response_body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Unable to reach Azure DevOps at {url}: {exc.reason}") from exc

    def list_projects(self) -> list[dict]:
        projects: list[dict] = []
        continuation_token = None

        while True:
            query = {"api-version": API_VERSION, "$top": 100}
            if continuation_token:
                query["continuationToken"] = continuation_token

            payload, headers = self._request("GET", "/_apis/projects", query=query)
            projects.extend(payload.get("value", []))
            continuation_token = headers.get("x-ms-continuationtoken")
            if not continuation_token:
                return projects

    def get_project_by_name(self, project_name: str) -> dict | None:
        for project in self.list_projects():
            if project.get("name", "").lower() == project_name.lower():
                return project
        return None

    def list_process_templates(self) -> list[dict]:
        payload, _ = self._request(
            "GET",
            "/_apis/process/processes",
            query={"api-version": PROCESS_API_VERSION},
        )
        return payload.get("value", [])

    def get_default_process_template_id(self) -> str:
        preferred_names = ["Agile", "Scrum", "Basic", "CMMI"]
        templates = self.list_process_templates()

        for preferred_name in preferred_names:
            for template in templates:
                if template.get("name", "").lower() == preferred_name.lower() and not template.get("isDisabled"):
                    template_id = template.get("typeId")
                    if template_id:
                        return template_id

        for template in templates:
            if not template.get("isDisabled") and template.get("typeId"):
                return template["typeId"]

        raise RuntimeError("No usable Azure DevOps process template was found in the target organization.")

    def create_project(self, project_name: str, process_template_id: str) -> dict:
        payload, _ = self._request(
            "POST",
            "/_apis/projects",
            body={
                "name": project_name,
                "capabilities": {
                    "versioncontrol": {"sourceControlType": "Git"},
                    "processTemplate": {"templateTypeId": process_template_id},
                },
            },
            query={"api-version": PROJECTS_API_VERSION},
        )
        return payload

    def wait_for_operation(self, operation_url: str, timeout_seconds: int = 600) -> None:
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            payload, _ = self._request_url("GET", operation_url)
            status = str(payload.get("status", "")).lower()

            if status == "succeeded":
                return
            if status in {"failed", "cancelled"}:
                message = payload.get("resultMessage") or payload.get("detailedMessage") or payload
                raise RuntimeError(f"Project creation did not complete successfully: {message}")

            time.sleep(5)

        raise RuntimeError("Timed out while waiting for Azure DevOps project creation to finish.")

    def list_repositories(self, project: str) -> list[dict]:
        repositories: list[dict] = []
        continuation_token = None

        while True:
            query = {"api-version": API_VERSION}
            if continuation_token:
                query["continuationToken"] = continuation_token

            payload, headers = self._request(
                "GET",
                f"/{parse.quote(project, safe='')}/_apis/git/repositories",
                query=query,
            )
            repositories.extend(payload.get("value", []))
            continuation_token = headers.get("x-ms-continuationtoken")
            if not continuation_token:
                return repositories

    def get_repository_by_name(self, project: str, repository_name: str) -> dict | None:
        for repository in self.list_repositories(project):
            if repository.get("name", "").lower() == repository_name.lower():
                return repository
        return None

    def create_repository(self, project: str, repository_name: str) -> dict:
        project_reference = self.get_project_by_name(project)
        project_payload = {"name": project}
        if project_reference and project_reference.get("id"):
            project_payload = {"id": project_reference["id"]}

        payload, _ = self._request(
            "POST",
            f"/{parse.quote(project, safe='')}/_apis/git/repositories",
            body={
                "name": repository_name,
                "project": project_payload,
            },
            query={"api-version": API_VERSION},
        )
        return payload


def parse_repo_names(raw_names: Iterable[str] | None) -> set[str]:
    if not raw_names:
        return set()

    repo_names: set[str] = set()
    for raw_value in raw_names:
        for item in raw_value.split(","):
            cleaned = item.strip()
            if cleaned:
                repo_names.add(cleaned.lower())
    return repo_names


def ensure_target_project(
    target_client: AzureDevOpsClient,
    project_name: str,
    dry_run: bool,
    process_template_id: str | None,
) -> str:
    existing_project = target_client.get_project_by_name(project_name)
    if existing_project:
        return "exists"

    if dry_run:
        return "would-create-project"

    if not process_template_id:
        process_template_id = target_client.get_default_process_template_id()

    operation = target_client.create_project(project_name, process_template_id)
    operation_url = operation.get("url")
    if not operation_url:
        raise RuntimeError(f"Target project creation response for {project_name} did not include an operation URL.")

    target_client.wait_for_operation(operation_url)
    return "created"


def get_project_repositories(
    client: AzureDevOpsClient,
    project_name: str,
    requested_repo_names: set[str],
) -> list[dict]:
    repositories = client.list_repositories(project_name)
    if not requested_repo_names:
        return repositories

    return [
        repository for repository in repositories if repository.get("name", "").lower() in requested_repo_names
    ]


def build_git_url(base_url: str, organization: str, project: str, repository_name: str) -> str:
    encoded_repo = parse.quote(repository_name, safe="")
    return (
        f"{base_url.rstrip('/')}/{parse.quote(organization, safe='')}/"
        f"{parse.quote(project, safe='')}/_git/{encoded_repo}"
    )


def run_git(arguments: list[str], authorization_header: str, cwd: Path | None = None) -> None:
    if not GIT:
        fail("Git is not installed or not available in PATH.")

    command = [GIT, "-c", f"http.extraHeader=Authorization: {authorization_header}", *arguments]
    result = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "Git command failed."
        stdout = result.stdout.strip()
        message = stderr if not stdout else f"{stderr}\n{stdout}"
        raise RuntimeError(message)


def run_git_capture(arguments: list[str], authorization_header: str, cwd: Path | None = None) -> str:
    if not GIT:
        fail("Git is not installed or not available in PATH.")

    command = [GIT, "-c", f"http.extraHeader=Authorization: {authorization_header}", *arguments]
    result = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip() or "Git command failed."
        stdout = result.stdout.strip()
        message = stderr if not stdout else f"{stderr}\n{stdout}"
        raise RuntimeError(message)
    return result.stdout


def iter_chunks(values: list[str], chunk_size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), chunk_size):
        yield values[index:index + chunk_size]


def push_migratable_refs(repo_dir: Path, target_authorization: str) -> None:
    refs_output = run_git_capture(
        ["for-each-ref", "--format=%(refname)"],
        target_authorization,
        cwd=repo_dir,
    )
    refspecs = [
        f"+{ref_name}:{ref_name}"
        for ref_name in refs_output.splitlines()
        if ref_name and not ref_name.startswith("refs/pull/")
    ]

    for refspec_batch in iter_chunks(refspecs, 100):
        run_git(["push", "origin", *refspec_batch], target_authorization, cwd=repo_dir)


def mirror_repository(
    source_client: AzureDevOpsClient,
    target_client: AzureDevOpsClient,
    source_project: str,
    target_project: str,
    repository_name: str,
    temp_root: Path,
    dry_run: bool,
    skip_existing: bool,
) -> str:
    source_url = build_git_url(source_client.base_url, source_client.organization, source_project, repository_name)
    target_url = build_git_url(target_client.base_url, target_client.organization, target_project, repository_name)

    target_repo = target_client.get_repository_by_name(target_project, repository_name)
    if target_repo and skip_existing:
        return "skipped-existing"

    if not target_repo:
        if dry_run:
            return "would-create-target-repo"
        target_client.create_repository(target_project, repository_name)

    if dry_run:
        return "would-mirror-push"

    repo_dir = temp_root / f"{repository_name}.git"
    safe_rmtree(repo_dir)

    run_git(["clone", "--mirror", source_url, str(repo_dir)], source_client.authorization)
    try:
        run_git(["config", "remote.origin.mirror", "false"], target_client.authorization, cwd=repo_dir)
        run_git(["remote", "set-url", "--push", "origin", target_url], target_client.authorization, cwd=repo_dir)
        push_migratable_refs(repo_dir, target_client.authorization)
    finally:
        safe_rmtree(repo_dir)

    return "migrated"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mirror Git repositories between Azure DevOps organizations or projects."
    )
    parser.add_argument("--source-org", help="Source Azure DevOps organization name.")
    parser.add_argument("--source-project", help="Optional source Azure DevOps project name.")
    parser.add_argument("--source-pat", help="Source Azure DevOps PAT. Defaults to AZDO_SOURCE_PAT.")
    parser.add_argument("--target-org", help="Target Azure DevOps organization name.")
    parser.add_argument("--target-project", help="Optional target Azure DevOps project name.")
    parser.add_argument("--target-pat", help="Target Azure DevOps PAT. Defaults to AZDO_TARGET_PAT.")
    parser.add_argument(
        "--base-url",
        help="Azure DevOps base URL. Defaults to AZDO_BASE_URL or https://dev.azure.com.",
    )
    parser.add_argument(
        "--env-file",
        default=ENV_FILE,
        help="Path to a .env file to load before resolving arguments.",
    )
    parser.add_argument(
        "--repo-names",
        nargs="*",
        help="Optional list of repo names to migrate. Comma-separated values are also accepted.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip repositories that already exist in the target project.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without creating or pushing anything.",
    )
    return parser.parse_args()


def validate_project_args(args: argparse.Namespace) -> None:
    if not args.source_org:
        fail("Missing source organization. Provide --source-org or set AZDO_SOURCE_ORG.")
    if not args.target_org:
        fail("Missing target organization. Provide --target-org or set AZDO_TARGET_ORG.")

    if bool(args.source_project) != bool(args.target_project):
        fail(
            "Provide both --source-project and --target-project for single-project migration, "
            "or omit both for org-wide migration."
        )


def resolve_pats(args: argparse.Namespace) -> tuple[str, str]:
    source_pat = args.source_pat or os.environ.get("AZDO_SOURCE_PAT")
    target_pat = args.target_pat or os.environ.get("AZDO_TARGET_PAT")
    if not source_pat:
        fail("Missing source PAT. Provide --source-pat or set AZDO_SOURCE_PAT.")
    if not target_pat:
        fail("Missing target PAT. Provide --target-pat or set AZDO_TARGET_PAT.")
    return source_pat, target_pat


def apply_env_defaults(args: argparse.Namespace) -> None:
    if not args.source_org:
        args.source_org = os.environ.get("AZDO_SOURCE_ORG")
    if not args.source_project:
        args.source_project = os.environ.get("AZDO_SOURCE_PROJECT")
    if not args.target_org:
        args.target_org = os.environ.get("AZDO_TARGET_ORG")
    if not args.target_project:
        args.target_project = os.environ.get("AZDO_TARGET_PROJECT")
    if not args.base_url:
        args.base_url = os.environ.get("AZDO_BASE_URL") or "https://dev.azure.com"


def build_project_pairs(
    args: argparse.Namespace,
    source_client: AzureDevOpsClient,
) -> list[tuple[str, str]]:
    try:
        if args.source_project:
            return [(args.source_project, args.target_project)]

        source_projects = source_client.list_projects()
    except RuntimeError as error_message:
        fail(f"Unable to list source projects: {error_message}")

    if not source_projects:
        fail(f"No projects were found in source organization {args.source_org}.")

    project_pairs = [
        (project.get("name"), project.get("name"))
        for project in source_projects
        if project.get("name")
    ]
    if not project_pairs:
        fail("No source projects were available for migration.")

    return project_pairs


def migrate_project_repositories(
    source_client: AzureDevOpsClient,
    target_client: AzureDevOpsClient,
    source_project_name: str,
    target_project_name: str,
    requested_repo_names: set[str],
    temp_root: Path,
    dry_run: bool,
    skip_existing: bool,
    process_template_id: str | None,
    failures: list[tuple[str, str]],
) -> tuple[int, int, int, int, int]:
    try:
        repositories = get_project_repositories(
            source_client,
            source_project_name,
            requested_repo_names,
        )
    except RuntimeError as error_message:
        failures.append((source_project_name, f"Unable to list repositories: {error_message}"))
        print(f"Project {source_project_name}: FAILED to list repositories: {error_message}")
        return 0, 0, 0, 0, 0

    if not repositories:
        print(f"Project {source_project_name}: no repositories matched; skipping.")
        return 0, 0, 0, 0, 0

    print(
        f"Project {source_project_name}: found {len(repositories)} repos. "
        f"Target project: {target_project_name}."
    )

    try:
        project_outcome = ensure_target_project(
            target_client=target_client,
            project_name=target_project_name,
            dry_run=dry_run,
            process_template_id=process_template_id,
        )
    except RuntimeError as error_message:
        failures.append((target_project_name, f"Unable to prepare target project: {error_message}"))
        print(f"  FAILED to prepare target project: {error_message}")
        return len(repositories), 0, 0, 0, 0

    projects_created = 0
    planned = 0
    if project_outcome == "created":
        projects_created = 1
        print("  Created target project")
    elif project_outcome == "would-create-project":
        planned = 1
        print("  would-create-project")

    migrated = 0
    skipped = 0
    for repository in repositories:
        repository_name = repository.get("name")
        if not repository_name:
            continue

        print(f"  Processing {repository_name}...")
        try:
            outcome = mirror_repository(
                source_client=source_client,
                target_client=target_client,
                source_project=source_project_name,
                target_project=target_project_name,
                repository_name=repository_name,
                temp_root=temp_root,
                dry_run=dry_run,
                skip_existing=skip_existing,
            )
        except RuntimeError as error_message:
            failures.append((f"{source_project_name}/{repository_name}", str(error_message)))
            print(f"    FAILED: {error_message}")
            continue

        if outcome == "migrated":
            migrated += 1
            print("    Migrated")
        elif outcome == "skipped-existing":
            skipped += 1
            print("    Skipped because target repo already exists")
        else:
            planned += 1
            print(f"    {outcome}")

    return len(repositories), migrated, skipped, planned, projects_created


def main() -> None:
    load_env_file(resolve_env_file_from_argv(sys.argv[1:]))
    args = parse_args()
    apply_env_defaults(args)

    validate_project_args(args)
    source_pat, target_pat = resolve_pats(args)

    source_client = AzureDevOpsClient(args.source_org, source_pat, args.base_url)
    target_client = AzureDevOpsClient(args.target_org, target_pat, args.base_url)

    requested_repo_names = parse_repo_names(args.repo_names)
    project_pairs = build_project_pairs(args, source_client)
    process_template_id = None

    total_source_repos = 0

    migrated = 0
    skipped = 0
    planned = 0
    projects_created = 0
    failures: list[tuple[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="azdo-repo-migrate-") as temp_dir:
        temp_root = Path(temp_dir)
        for source_project_name, target_project_name in project_pairs:
            (
                project_repo_count,
                project_migrated,
                project_skipped,
                project_planned,
                project_created_count,
            ) = migrate_project_repositories(
                source_client=source_client,
                target_client=target_client,
                source_project_name=source_project_name,
                target_project_name=target_project_name,
                requested_repo_names=requested_repo_names,
                temp_root=temp_root,
                dry_run=args.dry_run,
                skip_existing=args.skip_existing,
                process_template_id=process_template_id,
                failures=failures,
            )
            total_source_repos += project_repo_count
            migrated += project_migrated
            skipped += project_skipped
            planned += project_planned
            projects_created += project_created_count

    print()
    print(f"Projects scanned: {len(project_pairs)}")
    print(f"Projects created: {projects_created}")
    print(f"Source repos matched: {total_source_repos}")
    print(f"Migrated: {migrated}")
    print(f"Skipped: {skipped}")
    print(f"Dry-run actions: {planned}")
    print(f"Failed: {len(failures)}")

    if failures:
        for repository_name, reason in failures:
            print(f"- {repository_name}: {reason}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()