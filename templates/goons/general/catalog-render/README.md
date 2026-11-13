# general/catalog-render

Render the six explicitly reviewed Backstage Component values in `candidate_json`
to `catalog-info.yaml`. This complete standalone Python Goon uses native uv and
the existing owned Step workspace; it does not write the selected repository.
The declared environment input transports metadata without putting it in argv.
The formatter reads no repository source, Git, CODEOWNERS or credentials.

The same `oh_goon_catalog_renderer` source package supplies product preview through
native backend packaging. There is one validator/serializer. Field allowlisting
does not redact values; review every exported string. Output creation is exclusive.

`catalog-info.yaml` uses `retention: content_store` so an explicit
`runs export-artifact` can retrieve the reviewed descriptor after the owned Step
workspace is disposed. The original Run records its file identity and retained
reference; collection does not register or publish it to Backstage. The
[artifact retention guide](../../../../docs/development/TEMPLATE_ARTIFACT_RETENTION.md)
owns reference lifetime, availability checks and export authority.

Use native uv to install this project, then run its standard-library tests with
`uv run --frozen --no-sync python -m unittest discover -s tests`. The command also
accepts `catalog-render --input /absolute/path/to/reviewed.json` for a bounded local
fixture; it never falls back between file and environment input.
