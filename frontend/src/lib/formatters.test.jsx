import { describe, it, expect, vi } from "vitest";
import { extractFileAttachments, relativeTime } from "./formatters";

const PROJECT = "crowd-nav";

describe("relativeTime", () => {
  it("returns empty string for falsy input", () => {
    expect(relativeTime(null)).toBe("");
    expect(relativeTime("")).toBe("");
    expect(relativeTime(undefined)).toBe("");
  });

  it("treats timezone-naive ISO strings as UTC", () => {
    // 60 seconds ago in UTC, without Z suffix (as our backend returns)
    const now = new Date();
    const sixtySecsAgo = new Date(now.getTime() - 60_000);
    const naive = sixtySecsAgo.toISOString().replace("Z", "");
    const result = relativeTime(naive);
    expect(result).toBe("1m ago");
  });

  it("handles ISO strings with Z suffix", () => {
    const now = new Date();
    const twoMinAgo = new Date(now.getTime() - 120_000);
    expect(relativeTime(twoMinAgo.toISOString())).toBe("2m ago");
  });

  it("handles ISO strings with +00:00 offset", () => {
    const now = new Date();
    const fiveMinAgo = new Date(now.getTime() - 300_000);
    const withOffset = fiveMinAgo.toISOString().replace("Z", "+00:00");
    expect(relativeTime(withOffset)).toBe("5m ago");
  });

  it("shows seconds for < 60s", () => {
    const now = new Date();
    const tenSecsAgo = new Date(now.getTime() - 10_000);
    expect(relativeTime(tenSecsAgo.toISOString())).toBe("10s ago");
  });

  it("shows hours for >= 60m", () => {
    const now = new Date();
    const twoHrsAgo = new Date(now.getTime() - 7_200_000);
    expect(relativeTime(twoHrsAgo.toISOString())).toBe("2h ago");
  });

  it("shows days for >= 24h", () => {
    const now = new Date();
    const threeDaysAgo = new Date(now.getTime() - 259_200_000);
    expect(relativeTime(threeDaysAgo.toISOString())).toBe("3d ago");
  });
});

describe("extractFileAttachments", () => {
  it("returns empty array for null/empty text", () => {
    expect(extractFileAttachments(null, PROJECT)).toEqual([]);
    expect(extractFileAttachments("", PROJECT)).toEqual([]);
  });

  it("returns empty array when no file paths present", () => {
    expect(extractFileAttachments("Hello world, no files here.", PROJECT)).toEqual([]);
  });

  // --- Markdown image syntax ---

  it("detects markdown image syntax for images", () => {
    const text = "Check this out: ![screenshot](output/result.png)";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual({
      path: "output/result.png",
      resolvedUrl: "/api/files/crowd-nav/output/result.png",
      type: "image",
      ext: "png",
    });
  });

  it("detects markdown image syntax for video", () => {
    const text = "Here's the recording: ![demo](videos/demo.mp4)";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
    expect(result[0].type).toBe("video");
    expect(result[0].ext).toBe("mp4");
  });

  it("detects markdown image syntax for csv", () => {
    const text = "Results: ![data](results/metrics.csv)";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
    expect(result[0].type).toBe("csv");
  });

  // --- Backtick-wrapped paths ---

  it("detects backtick-wrapped paths", () => {
    const text = "I saved the plot to `output/chart.png` for you.";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
    expect(result[0].path).toBe("output/chart.png");
    expect(result[0].type).toBe("image");
  });

  it("detects backtick-wrapped video path", () => {
    const text = "Video at `recordings/test.webm`";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
    expect(result[0].type).toBe("video");
    expect(result[0].ext).toBe("webm");
  });

  it("ignores backtick paths without slash (bare filenames)", () => {
    // The backtick regex requires a / in the path
    const text = "File is `image.png`";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toEqual([]);
  });

  // --- Bare paths ---

  it("detects bare paths containing /", () => {
    const text = "Output saved to results/figure.jpg in the project.";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
    expect(result[0].path).toBe("results/figure.jpg");
    expect(result[0].type).toBe("image");
  });

  it("detects bare path at start of line", () => {
    const text = "data/output.csv has the results.";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
    expect(result[0].type).toBe("csv");
  });

  // --- Workspace prefix stripping ---

  it("strips /projects/{project}/ prefix from absolute workspace paths", () => {
    const text = "File at `/projects/crowd-nav/output/result.png`";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
    expect(result[0].path).toBe("output/result.png");
    expect(result[0].resolvedUrl).toBe("/api/files/crowd-nav/output/result.png");
  });

  // --- Deduplication ---

  it("deduplicates paths mentioned multiple times", () => {
    const text = [
      "I created `output/chart.png`",
      "The file output/chart.png looks good.",
    ].join("\n");
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
  });

  // --- Skip inline-rendered images ---

  it("skips full-line markdown images (already rendered by renderMarkdown)", () => {
    // A full-line ![...](path) is rendered inline by renderMarkdown
    const text = "![screenshot](output/result.png)";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(0);
  });

  it("skips bare filename on its own line (already rendered by renderMarkdown)", () => {
    const text = "screenshot.png";
    const result = extractFileAttachments(text, PROJECT);
    // bare filename without / won't match any regex anyway
    expect(result).toHaveLength(0);
  });

  it("does NOT skip markdown images that are inline within other text", () => {
    const text = "Check this out: ![img](output/result.png) pretty cool.";
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(1);
    expect(result[0].path).toBe("output/result.png");
  });

  // --- Multiple file types in one message ---

  it("detects mixed file types in a single message", () => {
    const text = [
      "I generated several files:",
      "- Image: `output/plot.png`",
      "- Video: `recordings/demo.mp4`",
      "- Data: `results/data.csv`",
    ].join("\n");
    const result = extractFileAttachments(text, PROJECT);
    expect(result).toHaveLength(3);
    expect(result.map((r) => r.type)).toEqual(["image", "video", "csv"]);
  });

  // --- All supported extensions ---

  it("detects all supported image extensions", () => {
    const exts = ["png", "jpg", "jpeg", "gif", "svg", "webp"];
    for (const ext of exts) {
      const text = `File at \`output/img.${ext}\``;
      const result = extractFileAttachments(text, PROJECT);
      expect(result).toHaveLength(1);
      expect(result[0].type).toBe("image");
      expect(result[0].ext).toBe(ext);
    }
  });

  it("detects all supported video extensions", () => {
    const exts = ["mp4", "webm", "mov"];
    for (const ext of exts) {
      const text = `File at \`output/vid.${ext}\``;
      const result = extractFileAttachments(text, PROJECT);
      expect(result).toHaveLength(1);
      expect(result[0].type).toBe("video");
      expect(result[0].ext).toBe(ext);
    }
  });

  // --- HTTP URLs pass through ---

  it("passes through http URLs without resolving", () => {
    const text = "Image at ![img](https://example.com/photo.jpg)";
    const result = extractFileAttachments(text, PROJECT);
    // full-line check: this is inline, not full-line, so not skipped
    expect(result).toHaveLength(1);
    expect(result[0].resolvedUrl).toBe("https://example.com/photo.jpg");
  });
});
