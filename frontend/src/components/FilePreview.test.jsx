import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import FileAttachments from "./FilePreview";

describe("FileAttachments", () => {
  it("renders nothing for empty attachments", () => {
    const { container } = render(<FileAttachments attachments={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing for null attachments", () => {
    const { container } = render(<FileAttachments attachments={null} />);
    expect(container.innerHTML).toBe("");
  });
});

describe("ImagePreview (via FileAttachments)", () => {
  it("renders an image with correct src and filename", () => {
    const attachments = [
      { path: "output/chart.png", resolvedUrl: "/api/files/proj/output/chart.png", type: "image", ext: "png" },
    ];
    render(<FileAttachments attachments={attachments} />);
    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("src", "/api/files/proj/output/chart.png");
    expect(img).toHaveAttribute("loading", "lazy");
    expect(screen.getByText("chart.png")).toBeInTheDocument();
  });

  it("hides image on error", async () => {
    const attachments = [
      { path: "output/broken.png", resolvedUrl: "/api/files/proj/output/broken.png", type: "image", ext: "png" },
    ];
    render(<FileAttachments attachments={attachments} />);
    const img = screen.getByRole("img");
    fireEvent.error(img);
    await waitFor(() => {
      expect(screen.queryByRole("img")).not.toBeInTheDocument();
    });
  });

  it("opens lightbox on click and closes on overlay click", async () => {
    const attachments = [
      { path: "output/photo.jpg", resolvedUrl: "/api/files/proj/output/photo.jpg", type: "image", ext: "jpg" },
    ];
    render(<FileAttachments attachments={attachments} />);

    // Click the thumbnail image to open lightbox
    fireEvent.click(screen.getByRole("img"));
    await waitFor(() => {
      expect(screen.getAllByRole("img")).toHaveLength(2);
    });

    // Click the overlay (the fixed backdrop div) to close
    const overlay = screen.getAllByRole("img")[1].closest(".fixed");
    fireEvent.click(overlay);
    await waitFor(() => {
      expect(screen.getAllByRole("img")).toHaveLength(1);
    });
  });
});

describe("VideoPreview (via FileAttachments)", () => {
  it("renders a video element with correct src", () => {
    const attachments = [
      { path: "output/demo.mp4", resolvedUrl: "/api/files/proj/output/demo.mp4", type: "video", ext: "mp4" },
    ];
    const { container } = render(<FileAttachments attachments={attachments} />);
    const video = container.querySelector("video");
    expect(video).toBeTruthy();
    expect(video.getAttribute("src")).toBe("/api/files/proj/output/demo.mp4");
    expect(video).toHaveAttribute("controls");
    expect(video).toHaveAttribute("preload", "metadata");
    expect(screen.getByText("demo.mp4")).toBeInTheDocument();
  });

  it("hides video on error", async () => {
    const attachments = [
      { path: "output/broken.mp4", resolvedUrl: "/api/files/proj/output/broken.mp4", type: "video", ext: "mp4" },
    ];
    const { container } = render(<FileAttachments attachments={attachments} />);
    const video = container.querySelector("video");
    fireEvent.error(video);
    await waitFor(() => {
      expect(container.querySelector("video")).not.toBeInTheDocument();
    });
  });
});

describe("CsvPreview (via FileAttachments)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a click-to-preview button initially", () => {
    const attachments = [
      { path: "data/results.csv", resolvedUrl: "/api/files/proj/data/results.csv", type: "csv", ext: "csv" },
    ];
    render(<FileAttachments attachments={attachments} />);
    expect(screen.getByRole("button")).toHaveTextContent("results.csv");
  });

  it("fetches and renders CSV table on click", async () => {
    const csvText = "Name,Score\nAlice,95\nBob,88\n";
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve(csvText),
    });

    const attachments = [
      { path: "data/results.csv", resolvedUrl: "/api/files/proj/data/results.csv", type: "csv", ext: "csv" },
    ];
    render(<FileAttachments attachments={attachments} />);
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(screen.getByText("Name")).toBeInTheDocument();
      expect(screen.getByText("Score")).toBeInTheDocument();
      expect(screen.getByText("Alice")).toBeInTheDocument();
      expect(screen.getByText("95")).toBeInTheDocument();
      expect(screen.getByText("Bob")).toBeInTheDocument();
      expect(screen.getByText("88")).toBeInTheDocument();
    });
  });

  it("shows error state on fetch failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({ ok: false });

    const attachments = [
      { path: "data/missing.csv", resolvedUrl: "/api/files/proj/data/missing.csv", type: "csv", ext: "csv" },
    ];
    render(<FileAttachments attachments={attachments} />);
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(screen.getByText("Failed to load")).toBeInTheDocument();
    });
  });

  it("shows row count when CSV has more than 20 rows", async () => {
    const header = "id,value";
    const rows = Array.from({ length: 30 }, (_, i) => `${i},${i * 10}`).join("\n");
    const csvText = header + "\n" + rows + "\n";
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve(csvText),
    });

    const attachments = [
      { path: "data/big.csv", resolvedUrl: "/api/files/proj/data/big.csv", type: "csv", ext: "csv" },
    ];
    render(<FileAttachments attachments={attachments} />);
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(screen.getByText("Showing 20 of 30 rows")).toBeInTheDocument();
    });
  });

  it("handles quoted CSV fields with commas", async () => {
    const csvText = 'Name,Description\n"Smith, John","Has a ""quote"""\n';
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      text: () => Promise.resolve(csvText),
    });

    const attachments = [
      { path: "data/quoted.csv", resolvedUrl: "/api/files/proj/data/quoted.csv", type: "csv", ext: "csv" },
    ];
    render(<FileAttachments attachments={attachments} />);
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(screen.getByText("Smith, John")).toBeInTheDocument();
      expect(screen.getByText('Has a "quote"')).toBeInTheDocument();
    });
  });
});

describe("Mixed attachments", () => {
  it("renders multiple attachment types together", () => {
    const attachments = [
      { path: "output/img.png", resolvedUrl: "/api/files/proj/output/img.png", type: "image", ext: "png" },
      { path: "output/vid.mp4", resolvedUrl: "/api/files/proj/output/vid.mp4", type: "video", ext: "mp4" },
      { path: "data/out.csv", resolvedUrl: "/api/files/proj/data/out.csv", type: "csv", ext: "csv" },
    ];
    const { container } = render(<FileAttachments attachments={attachments} />);
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(container.querySelector("video")).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveTextContent("out.csv");
  });
});
