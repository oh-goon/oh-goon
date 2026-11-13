# general/catalog-render

Generate a retained Backstage Component descriptor from explicitly reviewed
`candidate_json` through the original Python Goon, Run and artifact lifecycle.
This teaching Plan runs locally without an AI provider. Native Python package
preparation retains its existing dependency/network authority; this Plan does
not claim deny-all egress qualification. Export remains explicit.

Select the Plan with a complete `run.yml`. Replace the target with your exact
registered repository and review all six metadata values before preparing the
workflow. These synthetic values demonstrate the input shape; they do not supply
defaults or establish repository ownership:

```yaml
version: 2
source: {kind: catalog-plan, name: general/catalog-render}
target: {repository: /absolute/path/to/project}
inputs:
  candidate_json: >-
    {"name":"order-gateway","description":"Synthetic catalog example.","owner":"group:default/platform-team","type":"service","lifecycle":"experimental","languages":["python","typescript"]}
```

Prepare and run that selection through the ordinary workflow commands:

```sh
oh-goon workflow prepare ./workflow/run.yml
oh-goon run ./workflow/run.yml --no-input
```

The [Goon guide](../../../goons/general/catalog-render/README.md) describes the
formatter, retained output and explicit export boundary. The target remains the
Run's repository subject even though the formatter reads only reviewed metadata.
