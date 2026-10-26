# Independent application through an Atomic Goon

Classification: runnable-with-prerequisites

Keep the application in its own repository and maintain this ordinary Goon
separately. The application owns its implementation, native lock and installed
commands; it needs no `goon.yml` or Oh-Goon SDK. This example contains workflow
declarations only, with explicit placeholders to replace for your application.

First run the application's own native tests and installed command directly.
For a Python application, use its committed `uv.lock` and native uv commands.
Then set `application.path` and the actual installed argv in the
[Atomic Goon](goons/general/run/goon.yml). The [Plan](plan.yml) imports that
separately maintained Goon through the existing local catalog contract.
Keep `runtime: python` for managed uv, and select a dependency group only when the
invoked command needs it. For a plain application script, omit `runtime` and
`uv`, then name the application's own command and explicit interpreter instead.
Keep live dotenv state and other secrets outside the selected source; use
explicit environment imports. Capture refuses `.env`/`.env.*` runtime files,
while `.env.example`, `.env.sample` and `.env.template` remain authored templates.

Select the actual registered repository whose effects this workflow operates on:

```sh
oh-goon league add-folder /absolute/parent/of/your/repository
oh-goon league scan --json
```

Set that exact ID in [run.yml](run.yml). When the application itself is the
effect subject, select its real repository; an unrelated empty repository is not
a target-free execution mode. No repository-free subject is currently supported.
From this separate workflow directory, use the ordinary public commands:

```sh
oh-goon config inspect ./run.yml
oh-goon config validate ./run.yml
oh-goon lock ./run.yml
oh-goon run ./run.yml --json
oh-goon plan tape RUN_ID --json
oh-goon plan handoff RUN_ID --json
```

Application files run from a captured private attempt copy. Developer source and
`.venv` stay outside writable execution state. Frozen mode consumes the native
lock without claiming metadata freshness; `locked` is an explicit alternative.
The Run ID identifies the original outcome and cleanup evidence, regardless of
later source edits or checkout deletion.

For pinned Git source, replace the same `application` declaration with:

```yaml
application:
  git: https://github.com/your-org/application.git
  ref: main
  path: .
```

Use an exact contained subdirectory when the project is below repository root.
First run `oh-goon lock ./run.yml` to acquire its exact Git selection, then
inspect, validate and run it. Inspection never silently fetches missing source.
Ordinary lock/run retains the resolved commit after ref movement or cache loss;
only `oh-goon lock ./run.yml --update-applications` deliberately refreshes it.
Inspect the recorded application origin, path, commit and content identity in
Game Tape's Technical Step details. This is an application source selection,
not a Git Goon import: the application needs no catalog path or Goon files.

The [native integration cases](../../backend/tests/integration/services/test_independent_application_sources.py)
exercise independent local/Git source, native environment selection, actual
producer-to-consumer output and fresh-process history. Their local Linux scope
does not establish private Git authentication, model quality, strict custody,
WSL or target-free execution qualification.
