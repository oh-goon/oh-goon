import { useTranslation } from "react-i18next";

import type { CapturedProjectSource, UvExecutionEnvironment } from "@generated/models";

export function GameTapeProjectSource({ project, uv }: { project?: CapturedProjectSource | null; uv?: UvExecutionEnvironment | null }) {
  const { t } = useTranslation();
  if (!project && !uv) return null;
  const application = project?.application;
  return <section className="game-tape-provenance" aria-label={t("gameTape.inspector.projectSource")}>
    <strong>{t("gameTape.inspector.projectSource")}</strong>
    <dl className="game-tape-inspector__facts">
      {project ? <>
        <div className="game-tape-inspector__wide"><dt>{t("gameTape.inspector.projectContent")}</dt><dd><code>{project.content_sha256}</code></dd></div>
        <div className="game-tape-inspector__wide"><dt>{t("gameTape.inspector.projectArchive")}</dt><dd><code>{project.archive_sha256}</code></dd></div>
      </> : null}
      {application ? <>
        {"git" in application ? <>
          <div className="game-tape-inspector__wide"><dt>{t("gameTape.inspector.applicationOrigin")}</dt><dd><code>{application.git}</code></dd></div>
          <div><dt>{t("gameTape.inspector.applicationRef")}</dt><dd><code>{application.ref}</code></dd></div>
          <div className="game-tape-inspector__wide"><dt>{t("gameTape.inspector.applicationCommit")}</dt><dd><code>{application.commit}</code></dd></div>
        </> : null}
        <div className="game-tape-inspector__wide"><dt>{t("gameTape.inspector.applicationPath")}</dt><dd><code>{application.path}</code></dd></div>
      </> : null}
      {uv ? <>
        <div><dt>{t("gameTape.inspector.uvProject")}</dt><dd><code>{uv.project ?? "."}</code></dd></div>
        <div><dt>{t("gameTape.inspector.uvLockMode")}</dt><dd><code>{uv.lock_mode}</code></dd></div>
        <div><dt>{t("gameTape.inspector.uvGroup")}</dt><dd><code>{uv.group ?? t("gameTape.inspector.uvBaseOnly")}</code></dd></div>
      </> : null}
    </dl>
  </section>;
}
