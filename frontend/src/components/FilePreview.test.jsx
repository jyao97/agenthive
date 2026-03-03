import { describe, it, expect } from "vitest";
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
  it("renders a thumbnail image with .thumb.jpg src and filename", () => {
    const attachments = [
      { path: "output/demo.mp4", resolvedUrl: "/api/files/proj/output/demo.mp4", type: "video", ext: "mp4" },
    ];
    render(<FileAttachments attachments={attachments} />);
    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("src", "/api/files/proj/output/demo.mp4.thumb.jpg");
    expect(img).toHaveAttribute("loading", "lazy");
    expect(screen.getByText("demo.mp4")).toBeInTheDocument();
  });

  it("shows placeholder when thumbnail fails to load", async () => {
    const attachments = [
      { path: "output/broken.mp4", resolvedUrl: "/api/files/proj/output/broken.mp4", type: "video", ext: "mp4" },
    ];
    render(<FileAttachments attachments={attachments} />);
    const img = screen.getByRole("img");
    fireEvent.error(img);
    await waitFor(() => {
      expect(screen.queryByRole("img")).not.toBeInTheDocument();
    });
    // Filename text should still be visible
    expect(screen.getByText("broken.mp4")).toBeInTheDocument();
  });
});

describe("Mixed attachments", () => {
  it("renders multiple attachment types together", () => {
    const attachments = [
      { path: "output/img.png", resolvedUrl: "/api/files/proj/output/img.png", type: "image", ext: "png" },
      { path: "output/vid.mp4", resolvedUrl: "/api/files/proj/output/vid.mp4", type: "video", ext: "mp4" },
      { path: "data/readme.txt", resolvedUrl: "/api/files/proj/data/readme.txt", type: "doc", ext: "txt" },
    ];
    render(<FileAttachments attachments={attachments} />);
    // Image thumbnail + video thumbnail = 2 img elements
    expect(screen.getAllByRole("img")).toHaveLength(2);
    expect(screen.getByText("img.png")).toBeInTheDocument();
    expect(screen.getByText("vid.mp4")).toBeInTheDocument();
    // Doc file renders as a collapsible button
    expect(screen.getByRole("button")).toHaveTextContent("readme.txt");
  });
});
