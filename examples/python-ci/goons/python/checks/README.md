# python/checks

Run native Ruff and pytest against the selected Orders Python application.
The application's `ci` dependency group includes its ordinary development
tools and Ruff; native uv checks the committed lock before either command.
Its `pyproject.toml` owns the Ruff correctness-check selection.

The application path is relative to the Git Plan's selected authoring root.
For this repository the root is `.` and the application lives under
`templates/goons/general/orders-taskflow`. When adapting the complete recipe
to a project rooted at that authoring root, select `application.path: .`.

Ruff writes its native JSON diagnostics to `ruff.json`; pytest writes native
JUnit XML to `junit.xml`. These reports use content-store retention because an
external CI reader needs the actual diagnostics after a failed Step and scratch
disposal. This is a consumer choice for this recipe, rather than a catalog-wide
report policy. A lint or test failure remains a native failure and stops the
later wheel Goon. This verification workflow makes no strict executor or
resource limit claim.
