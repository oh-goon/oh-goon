# Independent application through an Atomic Goon

Classification: runnable-with-prerequisites

Keep the application in its own directory and maintain this ordinary Goon
separately. The application owns its implementation, native lock and installed
commands; it needs no `goon.yml` or Oh-Goon SDK. This example contains workflow
declarations only, with explicit placeholders to replace for your application.

First run the application's own native tests and installed command directly.
For a Python application, use its native `uv.lock` and native uv commands.
Then set `application.path` and the actual installed argv in the
[Atomic Goon](goons/general/run/goon.yml). The [Plan](plan.yml) imports that
separately maintained Goon through the existing local catalog contract.
Keep `runtime: python` for managed uv, and select a dependency group only when the
invoked command needs it. For a plain application script, omit `runtime` and
`uv`, then name the application's own command and explicit interpreter instead.
Keep live dotenv state and other secrets outside the selected source; use
explicit environment imports. Capture refuses `.env`/`.env.*` runtime files,
while `.env.example`, `.env.sample` and `.env.template` remain authored templates.

Register the actual application directory and initialize a fresh workflow with
its target identity. From the Oh-Goon source checkout:

```sh
oh-goon league add-folder /absolute/path/to/your/application
oh-goon init --template python/quality --kind plan --output /absolute/path/to/application-workflow --repository /absolute/path/to/your/application
cp examples/independent-application/plan.yml ../application-workflow/plan.yml
cp -R examples/independent-application/goons ../application-workflow/goons
```

Choose that absolute output path as the sibling `../application-workflow` used below; its parent must already exist.

Keep the generated `target` in `../application-workflow/run.yml`; replace its
`source` with `source: {kind: file-plan, path: ./plan.yml}` and remove any inputs
that do not belong to the custom Plan. Set the copied Goon's application path
and installed command as described above. This directory's [run.yml](run.yml)
illustrates the selection shape; copying its placeholder over the generated
target is unnecessary.

Git metadata is optional for an exact registered application root. The Run
still requires its real registered subject; unregistered or target-free
execution is unsupported. From `../application-workflow`, use the ordinary
public commands:

```sh
oh-goon workflow inspect ./run.yml
oh-goon workflow validate ./run.yml
oh-goon workflow prepare ./run.yml
oh-goon run ./run.yml --json
oh-goon runs tape RUN_ID --json
oh-goon runs handoff RUN_ID --json
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
First run `oh-goon workflow prepare ./run.yml` to acquire its exact Git selection, then
inspect, validate and run it. Inspection never silently fetches missing source.
Ordinary lock/run retains the resolved commit after ref movement or cache loss;
only `oh-goon workflow prepare ./run.yml --refresh applications` deliberately refreshes it.
Inspect the recorded application origin, path, commit and content identity in
Game Tape's Technical Step details. This is an application source selection,
not a Git Goon import: the application needs no catalog path or Goon files.

The [native integration cases](../../backend/tests/integration/services/test_independent_application_sources.py)
exercise independent local/Git source, native environment selection, actual
producer-to-consumer output and fresh-process history. Their local Linux scope
does not establish private Git authentication, model quality, strict custody,
WSL or target-free execution qualification.
