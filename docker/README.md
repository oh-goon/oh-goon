# Docker dependency authority

Docker is optional. Public source contains `backend/pyproject.toml`, committed `backend/uv.lock`, and committed frontend `package-lock.json`. Container builds require native uv to prove that `backend/uv.lock` is current with `uv sync --locked`; they never reconstruct or rewrite Python dependency resolution inside the image build. Release-wheel construction uses `uv build --no-sources` so uv-only source overrides cannot become hidden release authority. Runtime dependencies are exported from the already-checked lock and installed with hashes before the Oh-Goon wheel is installed with `--no-deps`.

## Acceptance bootstrap and cleanup

The locked Playwright container consumes the npm-selected image version and
uses `make init` for the existing locked uv/npm bootstrap and CLI smoke. Its
original `make acceptance-release` entrypoint retains full release/browser
acceptance. A native Make bootstrap proof does not qualify the assembled image
or that complete journey.

Product container acceptance owns a private temporary state/repository root and
one explicit Compose project. It runs the original build, installed-origin,
product, persistence, replacement and Game Tape readback flow. Final native
`compose down --remove-orphans --rmi local` must succeed before the owner removes
the root. Its CLI attempt is bounded to 60 seconds and streams Docker diagnostics.
A failed, unavailable, timed-out or interrupted teardown leaves state in place
and reports its path and project. Existing product issues remain visible;
primary exceptions and cancellation keep their identity, with cleanup notes also
rendered by the CLI. Successful container teardown followed by a filesystem
cleanup failure reports possibly partial removal, without hiding the original
failure or claiming intact retained state.

When cleanup is unproven, use native Compose with the reported project, original
Compose file/configuration and original bound directories to complete teardown.
Remove that owned retained root only after native teardown succeeds. The owner
does not automatically retry, prune by age or delete state beneath a possibly
active container.

[HUG-212](https://linear.app/hugloss/issue/HUG-212) retains the bounded native
cleanup reproduction and regression evidence. The controlled Docker fixture
qualifies this teardown/storage lifetime only; it does not establish full
product/release acceptance. Matching Playwright image assembly and full browser
acceptance remain separately selected. Host CLI cgroups do not establish
Docker/BuildKit daemon quotas; any container/build performance experiment needs
its own native resource observations.
