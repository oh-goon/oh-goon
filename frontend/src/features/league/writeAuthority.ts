export function destinationAvailable(
  targetKey: string,
  ownedKey: string,
  existingKeys: readonly string[],
): boolean {
  const target = targetKey.trim();
  if (target === "") return false;
  return target === ownedKey || !existingKeys.includes(target);
}

export function editorWriteAuthority(options: {
  catalogFresh: boolean;
  targetKey: string;
  ownedKey: string;
  targetReadFresh: boolean;
  existingKeys: readonly string[];
  editorStale?: boolean;
}): boolean {
  const target = options.targetKey.trim();
  const owned = target !== "" && target === options.ownedKey;
  return options.catalogFresh
    && (!owned || options.targetReadFresh)
    && destinationAvailable(target, options.ownedKey, options.existingKeys)
    && !options.editorStale;
}

export function templateReplaySafe(
  template: { sha256: string } | undefined,
  destination: { sha256: string } | undefined,
): boolean {
  return template !== undefined && (destination === undefined || destination.sha256 === template.sha256);
}
