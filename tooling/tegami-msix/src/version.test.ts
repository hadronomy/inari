import { expect, test } from "bun:test";

import { toMsixVersion } from "./version.ts";

test("maps release channels into monotonically ordered MSIX revisions", () => {
  expect(toMsixVersion("1.20.0-alpha.1")).toBe("1.20.0.1001");
  expect(toMsixVersion("1.20.0-beta.2")).toBe("1.20.0.2002");
  expect(toMsixVersion("1.20.0-rc.3")).toBe("1.20.0.3003");
  expect(toMsixVersion("1.20.0")).toBe("1.20.0.65535");
});

test("rejects prerelease channels Windows cannot order", () => {
  expect(() => toMsixVersion("1.20.0-preview.1")).toThrow("alpha, beta, and rc");
});
