# Adding an API

Routes should be thin:

1. validate the Python request model;
2. call one service operation;
3. return a typed model;
4. translate expected errors through the standard API error envelope.

Every frontend-consumed route must expose a stable semantic OpenAPI `operation_id` and an unambiguous successful response contract. The backend is authoritative for operation identity, method, path, parameters, body and response schema.

After changing a public contract:

```bash
make generate
make check-generated
```

The generation path is intentionally one-way:

```text
backend/
  -> FastAPI OpenAPI
  -> generated/openapi/openapi.json
  -> generated/frontend/
  -> frontend/
```

Do not edit generated files directly and do not duplicate method/path/schema metadata in the frontend.

Frontend raw network behavior belongs in `frontend/src/app/api/httpTransport.ts`. Mutation admission/result/diagnostics belong in `frontend/src/app/api/mutationTransport.ts`. `frontend/src/app/createApiClient.ts` maps generated operation identities to the readable framework-agnostic `frontend/src/domain/api/ApiClient.ts` surface and may shape ergonomic frontend arguments into generated path/query/body fields. Choosing read versus mutation control remains frontend policy and is not generated.

Feature code consumes the readable API through `useApiClient()` from `ApiClientProvider`.

Do not call `fetch` from features/components, create feature-specific backend providers, hand-write duplicate payload interfaces, copy HTTP paths or verbs into `createApiClient`, repeat request/response schema names, or bypass `MutationController` for writes.
