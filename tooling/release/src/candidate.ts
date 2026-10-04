import type { PackageDraft, PackageGraph } from "tegami";

import { release } from "./config.ts";

export function candidateVersion(
  version: string | undefined,
  sequence: string,
): string | undefined {
  if (!/^\d+$/.test(sequence) || Number(sequence) > 999) {
    throw new Error("The candidate sequence must be an integer from 0 through 999.");
  }
  const match = version?.match(/^(\d+\.\d+\.\d+)-alpha\.(\d+)$/);
  if (!match) return version;
  if (Number(sequence) < Number(match[2])) {
    throw new Error("The candidate sequence cannot precede the pending release version.");
  }
  return `${match[1]}-alpha.${Number(sequence)}`;
}

export function setCandidateSequence(draft: PackageDraft, sequence: string): void {
  const bumpVersion = draft.bumpVersion.bind(draft);
  draft.bumpVersion = (pkg) => candidateVersion(bumpVersion(pkg), sequence);
}

export function configureWindowsCandidate(
  graph: PackageGraph,
  drafts: ReadonlyMap<string, PackageDraft>,
  sequence: string,
): void {
  const id = "msix:inari-device-center";
  const pkg = graph.get(id);
  const pending = pkg && drafts.get(id)?.bumpVersion(pkg);
  if (!pkg?.version || !pending || pending === pkg.version) {
    throw new Error("A Windows candidate needs a pending Device Center version change.");
  }
  if (sequence) {
    for (const entry of drafts.values()) {
      setCandidateSequence(entry, sequence);
    }
  }
}

export async function prepareWindowsCandidate(sequence = ""): Promise<void> {
  // oxlint-disable-next-line no-underscore-dangle -- Tegami exposes the resolved package graph through this handle.
  const { graph } = await release._internal.context();
  const draft = await release.draft();
  configureWindowsCandidate(graph, draft.getPackageDrafts(), sequence);
  await draft.apply();
}
