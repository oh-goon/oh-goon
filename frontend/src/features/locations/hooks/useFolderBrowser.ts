import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { BrowseResponse } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface FolderBrowserState {
  resource: ReadResource<BrowseResponse>;
  browse(path?: string): void;
}

export function useFolderBrowser(open: boolean): FolderBrowserState {
  const client = useApiClient();
  const { t } = useTranslation();
  const [path, setPath] = useState<string | undefined>(undefined);
  const request = useCallback((signal: AbortSignal) => client.browse(path, { signal }), [client, path]);
  const { resource } = useReadResource({
    scope: "folder-browser",
    source: "GET /filesystem/browse",
    identity: path ?? "browser-root",
    enabled: open,
    errorMessage: t("errors.folderBrowse"),
    request,
    pollIntervalMs: 5_000,
    validate: (value) => {
      if (path !== undefined && value.path !== path) {
        throw new Error(`Folder browser identity mismatch: requested ${path}, received ${value.path}`);
      }
    },
  });
  return { resource, browse: setPath };
}
