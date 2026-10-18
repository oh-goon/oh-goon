import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useMutationBusy } from "@/app/ApiClientProvider";
import { advanceIdentityIncarnation, sameIdentityIncarnation, type IdentityIncarnation } from "@/domain/concurrency/identityIncarnation";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import type { BrowseResponse } from "@generated/models";
import { useAddLocation } from "@/features/locations/hooks/useAddLocation";
import { useFolderBrowser } from "@/features/locations/hooks/useFolderBrowser";
import { useRemoveLocation } from "@/features/locations/hooks/useRemoveLocation";

interface LocationManagerProps {
  locations: string[];
  revision: string;
  unavailableLocations?: string[];
  actionsDisabled?: boolean;
}

export function LocationManager({ locations, revision, unavailableLocations = [], actionsDisabled = false }: LocationManagerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const addFolderButtonRef = useRef<HTMLButtonElement | null>(null);
  const wasOpenRef = useRef(false);
  const browserOwnerRef = useRef<IdentityIncarnation>({ identity: "", generation: 0 });
  const busy = useMutationBusy();
  const browserState = useFolderBrowser(open);
  const browser = browserState.resource.data;
  browserOwnerRef.current = advanceIdentityIncarnation(browserOwnerRef.current, `${open ? "open" : "closed"}:${browser?.path ?? ""}`);
  const addLocation = useAddLocation();
  const removeLocation = useRemoveLocation();
  const disabled = actionsDisabled || busy;

  useEffect(() => {
    if (wasOpenRef.current && !open) addFolderButtonRef.current?.focus();
    wasOpenRef.current = open;
  }, [open]);

  async function addCurrent() {
    if (browser === null) return;
    const commandPath = browser.path;
    const commandOwner = browserOwnerRef.current;
    const result = await addLocation.add(commandPath);
    if (result.outcome === "saved" && sameIdentityIncarnation(browserOwnerRef.current, commandOwner)) setOpen(false);
  }

  return (
    <section className="panel">
      <div className="section-heading">
        <div><span className="eyebrow">{t("locations.eyebrow")}</span><h2>{t("locations.title")}</h2></div>
        <button ref={addFolderButtonRef} className="button button--secondary" type="button" disabled={disabled} aria-expanded={open} onClick={() => setOpen(true)}>
          {t("locations.addFolder")}
        </button>
      </div>
      {!open && browserState.resource.failure ? <ErrorBanner message={browserState.resource.failure.message} /> : null}
      <p className="muted">{t("locations.persistence")}</p>
      <div className="location-list">
        {locations.map((location) => (
          <div className="location-row" key={location}>
            <div><code>{location}</code>{unavailableLocations.includes(location) ? <span className="status-badge status-badge--warning">{t("locations.unavailableBadge")}</span> : null}</div>
            <button className="text-button" type="button" disabled={disabled} onClick={() => void removeLocation.remove(location, revision)}>
              {t("locations.remove")}
            </button>
          </div>
        ))}
      </div>
      {open ? <FolderBrowser
        browser={browser}
        loading={browserState.resource.status === "read-loading" || browserState.resource.status === "read-refreshing"}
        failure={browserState.resource.failure?.message ?? null}
        onBrowse={browserState.browse}
        onClose={() => setOpen(false)}
        onUse={addCurrent}
        disabled={disabled}
      /> : null}
    </section>
  );
}

interface FolderBrowserProps {
  browser: BrowseResponse | null;
  loading: boolean;
  failure: string | null;
  onBrowse: (path?: string) => void;
  onClose: () => void;
  onUse: () => Promise<void>;
  disabled: boolean;
}

function FolderBrowser({ browser, loading, failure, onBrowse, onClose, onUse, disabled }: FolderBrowserProps) {
  const { t } = useTranslation();
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => { headingRef.current?.focus(); }, []);
  return (
    <div
      className="folder-browser"
      role="dialog"
      aria-modal="true"
      aria-labelledby="league-folder-browser-title"
      aria-describedby="league-folder-browser-guidance"
      aria-busy={loading}
      onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); onClose(); } }}
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">{t("locations.chooseFolder")}</span>
          <h3 id="league-folder-browser-title" ref={headingRef} tabIndex={-1}>{t("locations.dialogTitle")}</h3>
          {browser ? <code>{browser.path}</code> : null}
        </div>
        <button className="text-button" type="button" onClick={onClose}>{t("locations.close")}</button>
      </div>
      <p className="muted" id="league-folder-browser-guidance">{t("locations.scanRule")}</p>
      {loading ? <p role="status" aria-live="polite">{t("locations.loadingFolders")}</p> : null}
      {failure ? <div role="alert"><ErrorBanner message={failure} /></div> : null}
      {browser ? <>
        {browser.directories.length === 0 ? <div className="notice notice--scope" role="status"><strong>{t("locations.noSubfoldersTitle")}</strong><p>{t("locations.noSubfoldersMessage")}</p></div> : null}
        <div className="folder-list" role="group" aria-label={t("locations.folderList")}>
          {browser.parent ? <button type="button" onClick={() => onBrowse(browser.parent ?? undefined)}>{t("locations.parent")}</button> : null}
          {browser.directories.map((directory) => (
            <button type="button" key={directory} onClick={() => onBrowse(directory)}>{directory.split(/[\\/]/).at(-1)}</button>
          ))}
        </div>
        <div className="folder-browser__actions">
          <button className="button button--primary" type="button" disabled={disabled || loading} onClick={() => void onUse()}>{t("locations.useFolder")}</button>
          <small>{t("locations.useFolderHint")}</small>
        </div>
      </> : null}
    </div>
  );
}
