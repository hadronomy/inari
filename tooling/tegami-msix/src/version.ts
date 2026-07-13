import semver from "semver";

const STABLE_REVISION = 65_535;
const PRERELEASE_OFFSETS: Record<string, number> = {
  alpha: 1_000,
  beta: 2_000,
  rc: 3_000,
};

export function toMsixVersion(version: string): string {
  const parsed = semver.parse(version);
  if (!parsed) throw new Error(`Invalid semantic version: ${version}.`);
  assertVersionPart(parsed.major, "major");
  assertVersionPart(parsed.minor, "minor");
  assertVersionPart(parsed.patch, "patch");

  if (parsed.prerelease.length === 0) {
    return `${parsed.major}.${parsed.minor}.${parsed.patch}.${STABLE_REVISION}`;
  }

  const [channel, rawSequence = 0] = parsed.prerelease;
  if (typeof channel !== "string" || !(channel in PRERELEASE_OFFSETS)) {
    throw new Error(`MSIX versions support only alpha, beta, and rc prereleases: ${version}.`);
  }
  const sequence = typeof rawSequence === "number" ? rawSequence : Number(rawSequence);
  if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence > 999) {
    throw new Error(`Prerelease sequence must be between 0 and 999: ${version}.`);
  }
  const revision = PRERELEASE_OFFSETS[channel]! + sequence;
  return `${parsed.major}.${parsed.minor}.${parsed.patch}.${revision}`;
}

function assertVersionPart(value: number, name: string): void {
  if (value > 65_535) throw new Error(`MSIX ${name} version cannot exceed 65535.`);
}
