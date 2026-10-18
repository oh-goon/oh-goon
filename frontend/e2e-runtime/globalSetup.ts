export default function globalSetup(): void {
  if (!process.env.OH_GOON_RUNTIME_ACCESS_TOKEN) {
    throw new Error("OH_GOON_RUNTIME_ACCESS_TOKEN is required");
  }
}
