# oh-goon/integration-contract

Run the existing repository `make test-integration` suite against a **producer-published
source workspace**. The required `source_workspace` input identifies the earlier Step
whose workspace must be explicitly transported to this consumer; it is not an
implicit repository checkout. Missing or invalid producer binding must fail before
Step work.

The suite itself is **not** a separate generated-API-only test implementation.
`integration-tests` invokes the same native Make target, but uses the admitted
repository workspace by default. These two capabilities are distinct because the
source provenance and resulting evidence differ, not because the command differs.

See the [self-hosting boundary contract](../../../../docs/development/TEMPLATE_SELF_HOSTING_BOUNDARIES.md).
