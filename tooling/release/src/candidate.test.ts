import { expect, test } from "bun:test";

import { candidateVersion } from "./candidate.ts";

test("advances alpha candidates without changing the release base", () => {
  expect(candidateVersion("1.20.0-alpha.12", "13")).toBe("1.20.0-alpha.13");
  expect(candidateVersion("1.20.0-alpha.12", "12")).toBe("1.20.0-alpha.12");
});

test("keeps independent stable packages and packages without versions", () => {
  expect(candidateVersion("0.3.2", "13")).toBe("0.3.2");
  expect(candidateVersion(undefined, "13")).toBeUndefined();
});

test("rejects an older sequence or an invalid MSIX prerelease sequence", () => {
  for (const sequence of ["11", "-1", "1.5", "1000", "alpha.13", ""]) {
    expect(() => candidateVersion("1.20.0-alpha.12", sequence)).toThrow();
  }
});
