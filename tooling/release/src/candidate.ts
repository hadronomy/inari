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

export async function prepareWindowsCandidate(sequence = ""): Promise<void> {
  const draft = await release.draft();
  if (!draft.hasPending()) throw new Error("A candidate needs pending release changes.");
  if (sequence) {
    for (const entry of draft.getPackageDrafts().values()) {
      const bumpVersion = entry.bumpVersion;
      entry.bumpVersion = (pkg) => candidateVersion(bumpVersion(pkg), sequence);
    }
  }
  await draft.apply();
}
