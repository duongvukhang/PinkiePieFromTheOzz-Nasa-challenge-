import { describe, expect, it, vi } from "vitest";
import { api } from "../../src/api/client";
import { parseViewParams, toViewQuery } from "../../src/viewer/state/viewStore";

describe("Project Workspace URL synchronization and API client", () => {
  it("parses and serializes projectId in viewer state", () => {
    const search = "?ra=133.79513&dec=-7.24515&projectId=proj-123-abc&fitsId=upload-1";
    const state = parseViewParams(search);
    expect(state.projectId).toBe("proj-123-abc");
    expect(state.ra).toBeCloseTo(133.79513);
    expect(state.dec).toBeCloseTo(-7.24515);

    const query = toViewQuery(state);
    expect(query).toContain("projectId=proj-123-abc");
    expect(query).toContain("ra=133.79513");
    expect(query).toContain("dec=-7.24515");
  });

  it("calls listProjects and createProject API endpoints", async () => {
    const mockProjects = {
      items: [
        {
          id: "p1",
          owner_id: "u1",
          name: "WISE 0855 Target Field",
          visibility: "private",
          current_version: 1,
          center_ra: 133.79513,
          center_dec: -7.24515,
          fov_arcmin: 10,
        },
      ],
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/v1/projects") && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        return new Response(
          JSON.stringify({
            id: "p2",
            owner_id: "u1",
            name: body.name,
            visibility: body.visibility || "private",
            current_version: 1,
            center_ra: body.center_ra,
            center_dec: body.center_dec,
            fov_arcmin: body.fov_arcmin,
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify(mockProjects), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    const listRes = await api.listProjects();
    expect(listRes.items).toHaveLength(1);
    expect(listRes.items[0]?.name).toBe("WISE 0855 Target Field");

    const created = await api.createProject({
      name: "Barnard's Star Motion",
      center_ra: 269.45208,
      center_dec: 4.69339,
      fov_arcmin: 10,
      visibility: "private",
    });
    expect(created.id).toBe("p2");
    expect(created.name).toBe("Barnard's Star Motion");

    fetchSpy.mockRestore();
  });

  it("handles project files linking, notes, and findings APIs", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/files") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            fits_file_id: "f-123",
            filename: "wise_w1.fits",
            epoch: "W1",
            mjd_tdb_mid: 60100.5,
            utc_mid: "2025-06-01T00:00:00Z",
            wcs_header: "",
            display_url: "/display.webp",
            science_url: "/science.bin",
            fits_url: "/frame.fits",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/notes") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            id: "n-1",
            project_id: "p1",
            body_md: "Observed proper motion vector",
            created_at: new Date().toISOString(),
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.includes("/findings") && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            id: "fd-1",
            project_id: "p1",
            title: "WISE 0855 Candidate",
            status: "candidate",
            summary_md: "Consistent with 8 arcsec/yr",
          }),
          { status: 201, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
    });

    const file = await api.linkProjectFile("p1", "f-123");
    expect(file.fits_file_id).toBe("f-123");
    expect(file.filename).toBe("wise_w1.fits");

    const note = await api.createNote("p1", { body_md: "Observed proper motion vector" });
    expect(note.id).toBe("n-1");
    expect(note.body_md).toBe("Observed proper motion vector");

    const finding = await api.createFinding("p1", {
      title: "WISE 0855 Candidate",
      status: "candidate",
      summary_md: "Consistent with 8 arcsec/yr",
    });
    expect(finding.id).toBe("fd-1");
    expect(finding.status).toBe("candidate");

    fetchSpy.mockRestore();
  });
});
