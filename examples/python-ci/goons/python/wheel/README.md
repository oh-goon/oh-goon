# python/wheel

Build the selected Orders application with native `uv build --wheel` after
the checks Goon succeeds. The application owns its build backend and package
metadata. This command uses a private captured application copy and retains
the declared wheel through the existing content store.

Native `uv build` has no `--locked` option. The preceding checks Goon owns the
native locked environment check; this Goon does not invent a build lock mode
or a dependency manager. It selects no managed runtime or dependency group.

The application path is relative to the Git Plan's authoring root. When
copying the recipe for another project, update that path and the exact wheel
artifact name to match the project's native build output. Use the recorded
Run and Step with `oh-goon runs export-artifact` to retrieve retained bytes
after execution scratch is disposed.
