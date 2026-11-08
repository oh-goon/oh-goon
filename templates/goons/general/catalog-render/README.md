# general/catalog-render

Render the six explicitly reviewed Backstage Component values in `candidate_json`
to `catalog-info.yaml`. This complete standalone Python Goon uses native uv and
the existing owned Step workspace; it does not write the selected repository.
The declared environment input transports metadata without putting it in argv.
The formatter reads no repository source, Git, CODEOWNERS or credentials.

The same `oh_goon_catalog_renderer` source package supplies product preview through
native backend packaging. There is one validator/serializer. Field allowlisting
does not redact values; review every exported string. Output creation is exclusive,
and the original Run retains its artifact. Export/publication is a separate effect.

Use native uv to install this project, then run its standard-library tests with
`uv run --frozen --no-sync python -m unittest discover -s tests`. The command also
accepts `catalog-render --input /absolute/path/to/reviewed.json` for a bounded local
fixture; it never falls back between file and environment input.
