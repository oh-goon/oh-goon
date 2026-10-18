# agents/adjudicate

Account for every finding and propose a coherent repair queue.

Read-only Agent. Uses `adjudication_skill`, `repo`, `goal` and `acceptance`. Every finding is explicitly repaired or left alone with a rationale; secondary evidence remains attached to its original review. The native loop owner decides whether another phase may start.
