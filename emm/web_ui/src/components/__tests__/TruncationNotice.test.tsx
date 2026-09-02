import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TruncationNotice } from "@/components/ui/TruncationNotice";

describe("TruncationNotice", () => {
  it("renders nothing when the full set is shown", () => {
    const { container } = render(<TruncationNotice shown={42} total={42} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when total is unknown and there is no more", () => {
    const { container } = render(<TruncationNotice shown={10} total={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows N из M when the page is truncated", () => {
    render(<TruncationNotice shown={200} total={613} />);
    expect(screen.getByText(/Показано 200 из 613/)).toBeInTheDocument();
    // No load-more button → text asks to narrow the filter.
    expect(screen.getByText(/уточните фильтр/)).toBeInTheDocument();
  });

  it("falls back to hasMore when total is unknown", () => {
    render(<TruncationNotice shown={50} hasMore />);
    expect(screen.getByText(/Показаны первые 50/)).toBeInTheDocument();
  });

  it("renders a load-more button and fires the callback", () => {
    const onLoadMore = vi.fn();
    render(
      <TruncationNotice shown={50} total={120} onLoadMore={onLoadMore} />,
    );
    const btn = screen.getByRole("button", { name: /Загрузить ещё/ });
    fireEvent.click(btn);
    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it("disables the load-more button while loading", () => {
    render(
      <TruncationNotice
        shown={50}
        total={120}
        onLoadMore={() => {}}
        loadingMore
      />,
    );
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
