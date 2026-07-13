import { afterEach, describe, expect, mock, spyOn, test } from "bun:test";

import { resolveOciArtifact } from "./registry.ts";

afterEach(() => {
  mock.restore();
});

describe("OCI chart resolution", () => {
  test("follows a Bearer challenge and returns the immutable chart layer", async () => {
    const requests: Request[] = [];
    installFetchMock(async (input, init) => {
      const request = new Request(input);
      if (init?.headers) {
        for (const [name, value] of new Headers(init.headers)) request.headers.set(name, value);
      }
      requests.push(request);
      if (request.url.startsWith("https://auth.example.test/token")) {
        return Response.json({ token: "registry-token" });
      }
      if (requests.filter(({ url }) => url.includes("/manifests/")).length === 1) {
        return new Response(null, {
          status: 401,
          headers: {
            "www-authenticate":
              'Bearer realm="https://auth.example.test/token?account=inari,release",service="registry.example.test",scope="repository:charts/inari:pull"',
          },
        });
      }
      expect(request.headers.get("authorization")).toBe("Bearer registry-token");
      return Response.json(
        {
          schemaVersion: 2,
          layers: [
            {
              mediaType: "application/vnd.cncf.helm.chart.content.v1.tar+gzip",
              digest: `sha256:${"b".repeat(64)}`,
            },
          ],
        },
        { headers: { "docker-content-digest": `sha256:${"a".repeat(64)}` } },
      );
    });

    await expect(
      resolveOciArtifact({
        registry: "registry.example.test",
        repository: "charts/inari",
        tag: "1.2.3",
      }),
    ).resolves.toEqual({
      digest: `sha256:${"a".repeat(64)}`,
      chartLayerDigest: `sha256:${"b".repeat(64)}`,
    });
  });

  test("treats a missing tag as pending", async () => {
    installFetchMock(async () => new Response(null, { status: 404 }));

    await expect(
      resolveOciArtifact({
        registry: "registry.example.test",
        repository: "charts/inari",
        tag: "1.2.3",
      }),
    ).resolves.toBeUndefined();
  });
});

function installFetchMock(
  implementation: (...args: Parameters<typeof fetch>) => Promise<Response>,
) {
  const fetchMock = (...args: Parameters<typeof fetch>) => implementation(...args);
  fetchMock.preconnect = () => undefined;
  spyOn(globalThis, "fetch").mockImplementation(fetchMock);
}
