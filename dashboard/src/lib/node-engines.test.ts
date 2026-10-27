/**
 * Guards the dashboard's Node runtime pin (2026-10-27).
 *
 * Vercel (Root Directory `dashboard`, auto-deploy on merge to main) reads
 * package.json `engines.node` ahead of the Project Settings version, and maps
 * an open range such as ">=22.18" to the NEWEST major it offers — so every new
 * Node major would reach production on an unrelated merge, untested. CI's
 * dashboard job pins setup-node, so the only safe shape is a single major
 * ("24.x") equal to that pin. Bumping the major is then one deliberate PR that
 * changes package.json, package-lock.json and ci.yml together.
 *
 * No YAML dependency: the dashboard job's block is sliced out of ci.yml by
 * indentation, which is all this check needs.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

const readText = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf8");

const pkg = JSON.parse(readText("../../package.json")) as {
  engines?: { node?: string };
};
const lock = JSON.parse(readText("../../package-lock.json")) as {
  packages?: Record<string, { engines?: { node?: string } }>;
};
const ciYml = readText("../../../.github/workflows/ci.yml").replace(
  /\r\n/g,
  "\n",
);

const ENGINES = pkg.engines?.node ?? "";
const SINGLE_MAJOR = /^(\d+)\.x$/;

/** The `node-version` values set inside ci.yml's `dashboard:` job. */
function ciDashboardNodeVersions(yml: string): string[] {
  // The job body runs until the next line indented 0-2 spaces (sibling job or
  // top-level key) or end of file.
  const job = yml.match(
    /^ {2}dashboard:[^\n]*\n([\s\S]*?)(?=^ {0,2}\S|(?![\s\S]))/m,
  );
  assert.ok(job, "ci.yml has no `dashboard:` job");
  return [
    ...job[1].matchAll(/^[ \t]*node-version:[ \t]*["']?([^"'\s#]+)/gm),
  ].map((m) => m[1]);
}

describe("engines.node", () => {
  test("pins exactly one Node major (N.x), never an open range", () => {
    assert.match(
      ENGINES,
      SINGLE_MAJOR,
      `engines.node is ${JSON.stringify(ENGINES)}; an open range lets Vercel ` +
        "float production to each new Node major that CI never ran",
    );
  });

  test("whole range supports native TS type stripping for `npm test`", () => {
    // Unflagged type stripping landed in 22.18 / 23.6, so 22.x and 23.x both
    // include versions where `node --test src/**/*.test.ts` cannot run.
    const major = Number(ENGINES.match(SINGLE_MAJOR)?.[1]);
    assert.ok(major >= 24, `engines.node major ${major} is below 24`);
  });

  test("matches the node-version CI's dashboard job tests on", () => {
    const versions = ciDashboardNodeVersions(ciYml);
    assert.equal(versions.length, 1, `expected one node-version, got ${versions}`);
    const ci = versions[0];
    assert.match(ci, /^\d+(\.x)?$/, `CI node-version ${ci} is not a fixed major`);
    assert.equal(
      ci.replace(/\.x$/, ""),
      ENGINES.replace(/\.x$/, ""),
      `CI tests Node ${ci} but Vercel deploys engines.node ${ENGINES}`,
    );
  });

  test("package-lock.json root entry carries the same engines", () => {
    assert.equal(lock.packages?.[""]?.engines?.node, ENGINES);
  });
});

describe("ciDashboardNodeVersions", () => {
  test("reads only the dashboard job, not neighbouring jobs", () => {
    const yml = [
      "jobs:",
      "  dashboard:",
      "    steps:",
      "      - uses: actions/setup-node@v4",
      "        with:",
      '          node-version: "24"',
      "  other:",
      "    steps:",
      "      - with:",
      "          node-version: 26",
      "",
    ].join("\n");
    assert.deepEqual(ciDashboardNodeVersions(yml), ["24"]);
  });

  test("handles the dashboard job being last in the file", () => {
    const yml = "jobs:\n  dashboard:\n    with:\n      node-version: '24.x'\n";
    assert.deepEqual(ciDashboardNodeVersions(yml), ["24.x"]);
  });
});
