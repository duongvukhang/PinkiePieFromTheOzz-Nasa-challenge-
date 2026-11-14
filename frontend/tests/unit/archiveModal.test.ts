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

  it("preserves fitsIds when constructing view query from store state", async () => {
    const { toViewQuery } = await import("../../src/viewer/state/viewStore");
    const query = toViewQuery({
      ra: 133.79513,
      dec: -7.24515,
      from: "2025-01-01",
      to: "2025-12-31",
      fitsId: "fits-arch-1",
      fitsIds: ["fits-arch-1", "fits-arch-2"],
    });
    expect(query).toContain("fitsId=fits-arch-1");
    expect(query).toContain("fitsIds=fits-arch-1%2Cfits-arch-2");
  });

  it("deduplicates cutouts within 0.5 days of loaded user frames", () => {
    const userFrames = [
      { frame_id: "u1", epoch: "Archive SPX-1", mjd_tdb_mid: 60500.1 },
    ];
    const rawCutouts = [
      { frame_id: "c1", epoch: "S1", mjd_tdb_mid: 60500.2 }, // |ΔMJD| = 0.1 < 0.5 -> should be replaced
      { frame_id: "c2", epoch: "S2", mjd_tdb_mid: 60680.0 }, // |ΔMJD| = 179.9 >= 0.5 -> kept
    ];

    const eligible = rawCutouts.filter(
      (cf) => !userFrames.some((uf) => Math.abs(cf.mjd_tdb_mid - uf.mjd_tdb_mid) < 0.5),
    );

    expect(eligible).toHaveLength(1);
    expect(eligible[0]?.frame_id).toBe("c2");
  });
});
