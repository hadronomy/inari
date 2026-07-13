import { z } from "zod";

const HELM_CHART_LAYER = "application/vnd.cncf.helm.chart.content.v1.tar+gzip";
const MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json";
const SHA256_DIGEST = /^sha256:[a-f0-9]{64}$/;

const registryTokenSchema = z
  .looseObject({
    token: z.string().optional(),
    access_token: z.string().optional(),
  })
  .transform(({ token, access_token }) => token ?? access_token)
  .pipe(z.string());

const manifestSchema = z.looseObject({
  schemaVersion: z.literal(2),
  layers: z.array(
    z.looseObject({
      mediaType: z.string(),
      digest: z.string().regex(SHA256_DIGEST),
    }),
  ),
});

const bearerChallengeSchema = z.looseObject({
  realm: z.url(),
  service: z.string().optional(),
  scope: z.string().optional(),
});

export interface OciReference {
  registry: string;
  repository: string;
  tag: string;
}

export interface OciArtifact {
  digest: string;
  chartLayerDigest: string;
}

export async function resolveOciArtifact(
  reference: OciReference,
): Promise<OciArtifact | undefined> {
  const url = `https://${reference.registry}/v2/${reference.repository}/manifests/${encodeURIComponent(reference.tag)}`;
  let response = await requestManifest(url);

  if (response.status === 401) {
    const challenge = response.headers.get("www-authenticate");
    const token = challenge ? await exchangeRegistryToken(challenge) : undefined;
    if (token) response = await requestManifest(url, token);
  }

  if (response.status === 404) return undefined;
  if (!response.ok) throw new Error(`Unable to resolve OCI artifact: ${response.status}.`);

  const digest = response.headers.get("docker-content-digest");
  if (!digest || !SHA256_DIGEST.test(digest)) {
    throw new Error("The registry response did not include an immutable SHA-256 digest.");
  }
  const manifest = manifestSchema.parse(await response.json());
  const chartLayer = manifest.layers.find((layer) => layer.mediaType === HELM_CHART_LAYER);
  if (!chartLayer) throw new Error("The OCI manifest does not contain a Helm chart layer.");
  return { digest, chartLayerDigest: chartLayer.digest };
}

function requestManifest(url: string, token?: string): Promise<Response> {
  const headers = new Headers({ Accept: MANIFEST_MEDIA_TYPE });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { headers });
}

async function exchangeRegistryToken(challenge: string): Promise<string | undefined> {
  const parameters = parseBearerChallenge(challenge);
  if (!parameters) return undefined;

  const tokenUrl = new URL(parameters.realm);
  if (parameters.service) tokenUrl.searchParams.set("service", parameters.service);
  if (parameters.scope) tokenUrl.searchParams.set("scope", parameters.scope);

  const headers = new Headers();
  const actor = process.env.GITHUB_ACTOR;
  const token = process.env.GITHUB_TOKEN;
  if (actor && token) {
    headers.set("Authorization", `Basic ${Buffer.from(`${actor}:${token}`).toString("base64")}`);
  }

  const response = await fetch(tokenUrl, { headers });
  if (!response.ok) {
    throw new Error(`Unable to authenticate to the OCI registry: ${response.status}.`);
  }
  return registryTokenSchema.parse(await response.json());
}

function parseBearerChallenge(
  challenge: string,
): z.infer<typeof bearerChallengeSchema> | undefined {
  const match = /^Bearer\s+(.+)$/i.exec(challenge);
  if (!match) return undefined;
  const parameters: Record<string, string> = {};
  for (const entry of match[1]!.matchAll(/([a-z][a-z0-9_-]*)="([^"]*)"/gi)) {
    parameters[entry[1]!.toLowerCase()] = entry[2]!;
  }
  return bearerChallengeSchema.parse(parameters);
}
