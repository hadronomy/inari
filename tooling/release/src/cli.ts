import { runCli } from "tegami/cli";

import { release } from "./config.ts";
import { readReleaseTargets } from "./targets.ts";

const [command] = process.argv.slice(2);

switch (command) {
  case "preview":
    await preview();
    break;
  case "status":
    await status();
    break;
  case "dry-run":
    await release.publish({ dryRun: true });
    break;
  case "targets":
    console.log(
      JSON.stringify(
        await readReleaseTargets(new URL("../../../.tegami/publish-lock.yaml", import.meta.url)),
      ),
    );
    break;
  default:
    await runCli(release);
}

async function preview(): Promise<void> {
  // Tegami exposes package graph inspection through this intentionally read-only handle.
  // oxlint-disable-next-line no-underscore-dangle
  const context = await release._internal.context();
  const draft = await release.draft();
  const changes = context.graph
    .getPackages()
    .map((pkg) => {
      const next = draft.getPackageDraft(pkg.id)?.bumpVersion(pkg);
      return next && next !== pkg.version
        ? { package: pkg.id, current: pkg.version, next }
        : undefined;
    })
    .filter((entry) => entry !== undefined);
  console.log(
    changes.length > 0 ? JSON.stringify(changes, null, 2) : "No pending release changes.",
  );
}

async function status(): Promise<void> {
  const result = await release.getPublishStatus();
  console.log(result.reason ? `${result.status}: ${result.reason}` : result.status);
  if (result.status === "pending") process.exitCode = 1;
}
