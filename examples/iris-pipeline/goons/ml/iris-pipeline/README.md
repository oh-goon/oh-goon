# ml/iris-pipeline

Run the existing standalone Iris project through four ordered native commands:
ingest, train, evaluate and publish. Set `application.path` to that project's
actual location. The application remains the same project used by the shipped
single-step Iris example; this Goon does not contain another copy.

The Goon owns input types/defaults and exact Step bindings. Each intermediate
JSON value has an explicit 16 KiB limit. DATA carries the selected split; MODEL
carries the fitted native estimator state; VERDICT carries measured acceptance.
Binary model bytes are not scalar outputs or implicit shared-workspace state.
`model_packet_sha256` identifies the canonical MODEL JSON; the content store
separately identifies the retained joblib bytes. Self-consistent packets do not
authenticate an external model or evaluation, and these small public values may
appear in arguments and Run evidence.

Evaluation failure stops the original Step sequence before publication. The
publisher independently refuses a rejected or mismatched verdict. Only its
three declared artifacts use retained content; output hashes and Step/Attempt
evidence remain with the normal Run and Game Tape owners.

The application uses native scikit-learn fitting, prediction, accuracy and
joblib serialization. This trusted-local teaching example is not strict
deployment/custody qualification or model-quality certification.
