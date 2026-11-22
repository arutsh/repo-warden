## MODIFIED Requirements

### Requirement: Opting a repo in with init
`devkit init` SHALL write a commented `.devkit.yaml` at the repo's top level, which opts the repo in. Optional flags SHALL write:
- `--agents-md`: `AGENTS.md` from the template, and `CLAUDE.md` containing `@AGENTS.md` if no `CLAUDE.md` exists;
- `--openspec`: generic openspec `rules:` and `operations:` guidance into `openspec/config.yaml`. They SHALL cover reusing or extending existing tests before adding new ones;
- `--ci`: a GitHub Actions workflow that runs `devkit check --range` on pull requests.

`init` SHALL never overwrite an existing file. An exception: for `--openspec` it SHALL add the `rules:` and `operations:` blocks only when the config has neither key, and otherwise print the snippet.

#### Scenario: Fresh repo
- **WHEN** `devkit init --agents-md` runs in a repo with neither file
- **THEN** `.devkit.yaml`, `AGENTS.md` and `CLAUDE.md` (containing `@AGENTS.md`) are created, and `devkit status` shows the repo as opted in

#### Scenario: Existing AGENTS.md
- **WHEN** `AGENTS.md` already exists
- **THEN** it is left unchanged and init says so

#### Scenario: openspec config with rules
- **WHEN** `openspec/config.yaml` already has a `rules:` key
- **THEN** the file is unchanged, and the suggested rules are printed

#### Scenario: openspec config with operations only
- **WHEN** `openspec/config.yaml` has an `operations:` key and no `rules:` key
- **THEN** the file is unchanged, and the suggested rules and operations are printed
