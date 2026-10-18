# Generated files

Generated artifacts have one unmistakable repository owner: `generated/`. They are reproducible output and must not be hand-edited.

```text
backend FastAPI routes + Pydantic models
  -> one OpenAPI capture
  -> generated/openapi/openapi.json
  -> backend/src/oh_goon/repository_tooling/generation/generate_api_types.py
  -> generated/frontend/schema.d.ts + schemaParts/
  -> generated/frontend/models.ts
  -> generated/frontend/enums.ts
  -> generated/frontend/operations.ts + operationTypes.ts
  -> generated/frontend/validators.ts + validatorRuntime.ts + validatorParts/
```

The source of truth for API semantics is the backend. The captured OpenAPI file is the exact handoff artifact; frontend generation reads that file rather than asking FastAPI for a second interpretation.

Use:

```bash
make generate
make check-generated
```

`make generate` first writes `generated/openapi/openapi.json`, then generates every frontend contract file from that exact document. `make check-generated` captures the backend contract once to verify the committed OpenAPI artifact and derives expected frontend files from the committed capture. The check does not rewrite files.

If generated output is wrong, change the backend source or generator. Never repair `generated/frontend/` manually.

Release build output is also reproducible and stages under `generated/release/`. Frontend builds must never copy output into `backend/src/oh_goon/`.
