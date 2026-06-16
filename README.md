# Azure DevOps Migration Runner

This repository orchestrates two migration steps with one command:

1. Mirror Git repositories from a source Azure DevOps org or project into a target org or project.
2. Run Azure DevOps Migration Tools (`devopsmigration`) using a temporary rendered config generated from the same `.env` file.

The main entrypoint is `run_all_migrations.sh`.

## What This Repository Does

This repo combines two separate migration flows:

- `migrate_azure_devops_repos.py` handles Git repository migration.
- `devopsmigration execute` handles Azure DevOps Migration Tools processors configured in `configuration.json`.

At the moment, `configuration.json` enables `AzureDevOpsPipelineProcessor`, so the second step is aimed at pipeline-related migration, not generic Git repo mirroring.

## Repository Files

- `run_all_migrations.sh`: Main launcher for the full migration flow.
- `migrate_azure_devops_repos.py`: Mirrors Git repositories from source to target.
- `render_devopsmigration_config.py`: Resolves environment placeholders and writes a temporary DevOpsMigration config.
- `configuration.json`: Template config for Azure DevOps Migration Tools.
- `.env.example`: Example environment file.
- `requirements.txt`: Minimal Python packaging dependencies.

## Prerequisites

You need these tools available in `PATH`:

- Python 3.10 or later
- Git
- Bash
- Azure DevOps Migration Tools CLI as `devopsmigration`

On Windows, use Git Bash, WSL with a working distro, or another real Bash runtime. A plain PowerShell session is not enough to run `run_all_migrations.sh` directly.

## Install Python Environment

The Python scripts use the standard library, but creating a virtual environment is still recommended.

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Install Azure DevOps Migration Tools

Official Windows installation options from the Azure DevOps Migration Tools documentation:

### `winget`

```powershell
winget install nkdAgility.AzureDevOpsMigrationTools
```

### `choco`

```powershell
choco install vsts-sync-migrator
```

### Manual install

Download the latest release and place `devopsmigration.exe` on your `PATH`.

References:

- https://devopsmigration.io/docs/setup/installation/
- https://github.com/nkdAgility/azure-devops-migration-tools/releases/latest

## Configure The Environment

This repository uses one `.env` file for both migration steps.

Create it from the example file:

### macOS or Linux

```bash
cp .env.example .env
```

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

Update the values:

```env
AZDO_SOURCE_PAT=your-source-pat
AZDO_SOURCE_ORG=your-source-org
AZDO_SOURCE_PROJECT=your-source-project
AZDO_TARGET_PAT=your-target-pat
AZDO_TARGET_ORG=your-target-org
AZDO_TARGET_PROJECT=your-target-project
AZDO_BASE_URL=https://dev.azure.com
```

## Environment Variables

- `AZDO_SOURCE_PAT`: PAT used to read source projects and repositories.
- `AZDO_SOURCE_ORG`: Source Azure DevOps organization short name, for example `contoso`.
- `AZDO_SOURCE_PROJECT`: Source project name.
- `AZDO_TARGET_PAT`: PAT used to create or update resources in the target.
- `AZDO_TARGET_ORG`: Target Azure DevOps organization short name.
- `AZDO_TARGET_PROJECT`: Target project name.
- `AZDO_BASE_URL`: Azure DevOps base URL, usually `https://dev.azure.com`.

## PAT Requirements

Typical permission needs are:

- Source PAT: read access to source projects and repositories.
- Target PAT: create project, create repository, and push Git refs.
- DevOpsMigration PATs: enough rights for every enabled processor in `configuration.json`.

Official permissions guidance:

- https://devopsmigration.io/docs/setup/permissions/

## How The Config Works

`configuration.json` is a template file. It contains placeholders such as:

```json
"AccessToken": "${AZDO_SOURCE_PAT}"
```

Before `devopsmigration execute` runs, `render_devopsmigration_config.py` creates a temporary JSON file with all placeholders resolved from `.env`.

That means:

- Do not store real PATs directly in `configuration.json`.
- Do not run `devopsmigration execute --config configuration.json` directly unless you render it first.
- Use `run_all_migrations.sh` unless you intentionally want to run the steps separately.

## Quick Start

Validate the setup first:

```bash
./run_all_migrations.sh --check
```

Run a safe repo dry run:

```bash
./run_all_migrations.sh --dry-run
```

Run the full migration:

```bash
./run_all_migrations.sh
```

Recommended order:

1. `./run_all_migrations.sh --check`
2. `./run_all_migrations.sh --dry-run`
3. `./run_all_migrations.sh`

## Main Commands

Run both migration steps:

```bash
./run_all_migrations.sh
```

Validate tooling and config rendering only:

```bash
./run_all_migrations.sh --check
```

Use a custom environment file:

```bash
./run_all_migrations.sh --env-file ./my-migration.env
```

Validate with a custom environment file:

```bash
./run_all_migrations.sh --check --env-file ./my-migration.env
```

## Repository Migration Options

These arguments are passed through to `migrate_azure_devops_repos.py`:

```bash
./run_all_migrations.sh --dry-run
./run_all_migrations.sh --skip-existing
./run_all_migrations.sh --repo-names RepoA RepoB
```

Examples:

```bash
./run_all_migrations.sh --skip-existing --repo-names Terraform FormResponsesAPI
./run_all_migrations.sh --dry-run --env-file ./preprod.env
```

## Run Each Step Separately

Run only repository migration:

```bash
python migrate_azure_devops_repos.py --env-file ./.env
```

Render the DevOpsMigration config manually:

```bash
python render_devopsmigration_config.py --env-file ./.env --output ./resolved.configuration.json
```

Run DevOpsMigration with the rendered config:

```bash
devopsmigration execute --config ./resolved.configuration.json
```

## What Changes And Where

- Source organization and project data are read from source.
- New projects, new repositories, and pushed Git refs are written to target.
- Azure DevOps Migration Tools processors write to the configured target endpoint.
- If source and target resolve to the same org and project, writes will land in that same location.

Verify `.env` carefully before a live run.

## Current Processor Scope

The current `configuration.json` enables:

- `AzureDevOpsPipelineProcessor`

With the current file, that means the DevOpsMigration step is configured for pipeline-related migration settings such as build pipelines, release pipelines, variable groups, and task groups.

If you later enable additional processors, update the permissions and migration expectations accordingly.

## Troubleshooting

### `devopsmigration: command not found`

Install Azure DevOps Migration Tools and confirm the executable is on `PATH`.

### `Python was not found in PATH`

Verify one of these works:

```bash
python --version
```

```bash
python3 --version
```

### Missing environment values

If the config renderer fails, check `.env` for missing, blank, or misspelled keys.

### Authentication or authorization failures

Recheck PAT scope, PAT expiry, org names, project names, and whether the source and target resources actually exist.

### Bash issues on Windows

If `run_all_migrations.sh` does not start correctly on Windows, run it from Git Bash or a working WSL environment instead of `bash.exe` shims that do not have a real distro behind them.

## Security Notes

- Treat `.env` as sensitive because it contains PATs.
- Do not commit real PATs to source control.
- Rotate PATs if they were ever stored in plain JSON or shared insecurely.