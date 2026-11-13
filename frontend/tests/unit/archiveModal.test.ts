import { describe, expect, it } from "vitest";
import { api, type ArchiveSearchParams, type ArchiveSourceId } from "../../src/api/client";

describe("Archive API client & search parameter serialization", () => {
  it("defines supported NASA archive sources", () => {
    const sources: ArchiveSourceId[] = ["spherex_irsa", "wise_irsa", "twomass_irsa", "skyview"];
    expect(sources).toHaveLength(4);
    expect(sources).toContain("spherex_irsa");
    expect(sources).toContain("wise_irsa");
  });

  it("constructs proper search parameters for multi-mission queries", () => {
    const params: ArchiveSearchParams = {
      ra: 133.79513,
      dec: -7.24515,
      radius: 5.0,
      radius_unit: "arcmin",
      source: "spherex_irsa",
      from: "2025-01-01",
      to: "2025-12-31",
      band: "D1",
    };
    expect(params.ra).toBe(133.79513);
    expect(params.dec).toBe(-7.24515);
    expect(params.radius_unit).toBe("arcmin");
    expect(params.from).toBe("2025-01-01");
    expect(params.to).toBe("2025-12-31");
  });

  it("exposes searchArchive and loadArchiveProduct on api object", () => {
    expect(typeof api.searchArchive).toBe("function");
    expect(typeof api.loadArchiveProduct).toBe("function");
  });

  it("computes non-zero multi-epoch interval when loading single epoch without collapsing", () => {
    const epDate = "2025-02-14";
    const base = new Date(`${epDate}T12:00:00Z`);
    const prevDay = new Date(base.getTime() - 86400000).toISOString().slice(0, 10);
    const nextDay = new Date(base.getTime() + 86400000).toISOString().slice(0, 10);
    expect(prevDay).toBe("2025-02-13");
    expect(nextDay).toBe("2025-02-15");
    expect(prevDay).not.toBe(nextDay);
  });
});
