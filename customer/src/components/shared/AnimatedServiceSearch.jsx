
import { useEffect, useRef, useState } from "react";
import { Search, X, ArrowUpRight } from "lucide-react";
import "./AnimatedServiceSearch.css";

const SERVICES = [
  "GST Return Filing",
  "Income Tax Return Filing",
  "TDS & TCS",
  "ROC & MCA Compliance",
  "Accounting & Bookkeeping",
  "Business Registration",
  "Payroll Services",
  "Audit Services",
  "NRI & International Tax",
  "Tax Notices",
];

export default function AnimatedServiceSearch({
  onSelectService,
  onSearch,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef(null);

  const results = query.trim()
    ? SERVICES.filter((service) =>
        service.toLowerCase().includes(query.trim().toLowerCase())
      )
    : [];

  useEffect(() => {
    if (isOpen) {
      const timer = window.setTimeout(() => {
        inputRef.current?.focus();
      }, 250);

      return () => window.clearTimeout(timer);
    }
  }, [isOpen]);

  function openSearch() {
    setIsOpen(true);
  }

  function closeSearch() {
    setIsOpen(false);
    setQuery("");
  }

  function handleSubmit(event) {
    event.preventDefault();

    const value = query.trim();
    if (!value) {
      inputRef.current?.focus();
      return;
    }

    if (onSearch) {
      onSearch(value);
    } else if (results.length === 1 && onSelectService) {
      onSelectService(results[0]);
    }
  }

  function selectService(service) {
    if (onSelectService) {
      onSelectService(service);
    } else {
      setQuery(service);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Escape") {
      closeSearch();
    }
  }

  return (
    <div
      className={`ntaxco-search ${isOpen ? "is-open" : ""}`}
      onKeyDown={handleKeyDown}
    >
      <button
        type="button"
        className="ntaxco-search-trigger"
        onClick={openSearch}
        aria-label="Open service search"
        aria-expanded={isOpen}
        title="Search services"
      >
        <Search size={21} strokeWidth={2.2} />
      </button>

      <form
        className="ntaxco-search-form"
        onSubmit={handleSubmit}
        aria-hidden={!isOpen}
      >
        <Search
          className="ntaxco-search-leading-icon"
          size={19}
        />

        <input
          ref={inputRef}
          className="ntaxco-search-input"
          type="search"
          placeholder="Search services..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          tabIndex={isOpen ? 0 : -1}
          autoComplete="off"
          aria-label="Search tax and business services"
        />

        {query.trim() && (
          <button
            type="submit"
            className="ntaxco-search-submit"
            aria-label="Search"
            tabIndex={isOpen ? 0 : -1}
          >
            <ArrowUpRight size={19} />
          </button>
        )}

        <button
          type="button"
          className="ntaxco-search-close"
          onClick={closeSearch}
          aria-label="Close service search"
          tabIndex={isOpen ? 0 : -1}
        >
          <X size={23} strokeWidth={2.4} />
        </button>
      </form>

      {isOpen && query.trim() && (
        <div className="ntaxco-search-results">
          {results.length > 0 ? (
            <>
              <p className="ntaxco-search-results-heading">
                MATCHING SERVICES
              </p>

              {results.map((service) => (
                <button
                  key={service}
                  type="button"
                  className="ntaxco-search-result"
                  onClick={() => selectService(service)}
                >
                  <span className="ntaxco-search-result-icon">
                    <Search size={16} />
                  </span>

                  <span>{service}</span>

                  <ArrowUpRight
                    size={16}
                    className="ntaxco-search-result-arrow"
                  />
                </button>
              ))}
            </>
          ) : (
            <p className="ntaxco-search-empty">
              No matching services found. Try GST, Income Tax or TDS.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
