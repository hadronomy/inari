import { describe, expect, test } from "bun:test";

import { selectWorkflowPlan } from "./workflow.ts";

const edgeRelease = { edge: true, controllerChart: false };

describe("release workflow planning", () => {
  test("versions pending changes before inspecting an older publish plan", () => {
    expect(selectWorkflowPlan(true, "pending", edgeRelease)).toEqual({
      publish: false,
      edge: false,
      controllerChart: false,
    });
  });

  test("resumes an interrupted publish when no version changes are pending", () => {
    expect(selectWorkflowPlan(false, "pending", edgeRelease)).toEqual({
      publish: true,
      edge: true,
      controllerChart: false,
    });
  });

  test("does nothing when neither versioning nor publishing is pending", () => {
    expect(selectWorkflowPlan(false, "success", edgeRelease)).toEqual({
      publish: false,
      edge: false,
      controllerChart: false,
    });
  });
});
