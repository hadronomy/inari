import type { Tegami } from "tegami";

import { readReleaseTargets, type ReleaseTargets } from "./targets.ts";

type PublishStatus = Awaited<ReturnType<Tegami["getPublishStatus"]>>["status"];

export interface WorkflowPlan extends ReleaseTargets {
  publish: boolean;
}

const NO_TARGETS: ReleaseTargets = { edge: false, controllerChart: false };

export async function resolveWorkflowPlan(
  release: Tegami,
  publishLock: URL,
): Promise<WorkflowPlan> {
  const draft = await release.draft();
  if (draft.hasPending()) return selectWorkflowPlan(true, "pending", NO_TARGETS);

  const { status } = await release.getPublishStatus();
  const targets = status === "pending" ? await readReleaseTargets(publishLock) : NO_TARGETS;
  return selectWorkflowPlan(false, status, targets);
}

export function selectWorkflowPlan(
  hasPendingDraft: boolean,
  publishStatus: PublishStatus,
  targets: ReleaseTargets,
): WorkflowPlan {
  if (hasPendingDraft || publishStatus !== "pending") {
    return { publish: false, ...NO_TARGETS };
  }
  return publishPlan(targets);
}

function publishPlan(targets: ReleaseTargets): WorkflowPlan {
  return { publish: true, ...targets };
}
