# Frontend test support

Frontend behavior tests are colocated with the authored source they exercise under `src/`.

This directory is **support-only**:

```text
tests/support/  shared service-free frontend harnesses and fakes
```

Do not create `tests/unit/`, `tests/features/`, or behavior `*.test.ts(x)` files here. Put a behavior test beside its `app/`, `domain/`, `components/`, or `features/<feature>/` owner.
