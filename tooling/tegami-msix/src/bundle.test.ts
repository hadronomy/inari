import { expect, test } from "bun:test";

import { artifactNames, parseChecksums } from "./bundle.ts";

test("publishes the complete signing trust chain", () => {
  expect(artifactNames({ version: "1.20.0-alpha.1" })).toEqual([
    "Inari-Device-Center_1.20.0-alpha.1_x64.exe",
    "Inari-Device-Center_1.20.0-alpha.1_x64.msix",
    "Inari-Device-Center_1.20.0-alpha.1_x64.spdx.json",
    "hadronomy-code-signing-root.cer",
    "hadronomy-code-signing-root-fingerprint.txt",
    "inari-code-signing-issuer.cer",
    "inari-code-signing-issuer-fingerprint.txt",
  ]);
});

test("parses checksums written with Windows line endings", () => {
  const digest = "a".repeat(64);

  expect(parseChecksums(`${digest}  Device-Center.msix\r\n`)).toEqual(
    new Map([["Device-Center.msix", `sha256:${digest}`]]),
  );
});
