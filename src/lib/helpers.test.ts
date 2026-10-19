import { describe, expect, it } from "vitest";
import { rowsToCsv, scenarioChanged, setScenarioWinner } from "./helpers";

describe("scenario helpers", () => {
  it("keeps winner overrides separate and removes a cleared selection", () => {
    const original = { "stage1:r1:a::b": "a" };
    const updated = setScenarioWinner(original, "stage1:r1:a::b", "b");
    expect(original["stage1:r1:a::b"]).toBe("a");
    expect(updated["stage1:r1:a::b"]).toBe("b");
    expect(setScenarioWinner(updated, "stage1:r1:a::b", null)).toEqual({});
  });

  it("detects scenario changes regardless of key order", () => {
    expect(scenarioChanged({ b: "2", a: "1" }, { a: "1", b: "2" })).toBe(false);
    expect(scenarioChanged({ a: "2" }, { a: "1" })).toBe(true);
  });
});

describe("prediction export", () => {
  it("quotes CSV fields and escapes embedded quotes", () => {
    expect(rowsToCsv([{ team: 'A "quoted" name', probability: 0.625 }])).toBe('"team","probability"\n"A ""quoted"" name","0.625"');
  });
});
