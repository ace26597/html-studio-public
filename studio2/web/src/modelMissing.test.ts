import { describe, expect, it } from "vitest";
import { modelLabel, modelMissing } from "./Chat";
import type { Connection, Project } from "./types";

const connection = (id: string, label: string): Connection =>
  ({ id, label, kind: "gateway", available: true, default_model: "m1", models: [{ id: "m1", label: `${label} model` }] }) as unknown as Connection;
const project = (connectionId: string, model: string | null = null): Project =>
  ({ id: "p_1", name: "Page", connection: connectionId, model }) as unknown as Project;

describe("a project set to a connection this studio does not have", () => {
  const here = [connection("gateway", "Gateway")];

  it("is noticed, and not while the list of connections is still empty", () => {
    expect(modelMissing(project("subscription"), here)).toBe(true);
    expect(modelMissing(project("gateway"), here)).toBe(false);
    expect(modelMissing(project("subscription"), [])).toBe(false);
  });

  it("is not shown as another connection's model", () => {
    expect(modelLabel(project("subscription", "opus"), here)).toBe("Choose one");
    expect(modelLabel(project("gateway"), here)).toBe("Gateway model");
  });
});
