"use client";

import { SearchIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { SEARCH_MAX_LENGTH, useCatalogueCategories } from "../api";

interface CatalogueFiltersProps {
  search: string;
  onSearchChange: (value: string) => void;
  category: string | null;
  onCategoryChange: (value: string | null) => void;
}

function CategoryChip({
  label,
  pressed,
  onClick,
}: {
  label: string;
  pressed: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        "inline-flex h-11 shrink-0 items-center rounded-full border px-4 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
        pressed
          ? "border-primary bg-primary text-primary-foreground"
          : "bg-card text-foreground hover:bg-secondary",
      )}
    >
      {label}
    </button>
  );
}

/** Search box and category filter. Categories come from the backend. */
export function CatalogueFilters({
  search,
  onSearchChange,
  category,
  onCategoryChange,
}: CatalogueFiltersProps) {
  const categories = useCatalogueCategories();

  return (
    <div className="flex flex-col gap-3">
      <div role="search" className="relative">
        <label htmlFor="catalogue-search" className="sr-only">
          Search products
        </label>
        <SearchIcon
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 left-3 size-5 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          id="catalogue-search"
          type="search"
          inputMode="search"
          enterKeyHint="search"
          autoComplete="off"
          placeholder="Search by product, brand or SKU"
          maxLength={SEARCH_MAX_LENGTH}
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          className="pr-12 pl-10 [&::-webkit-search-cancel-button]:hidden"
        />
        {search ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="absolute top-1/2 right-0.5 -translate-y-1/2"
            onClick={() => onSearchChange("")}
          >
            <XIcon aria-hidden="true" />
            <span className="sr-only">Clear search</span>
          </Button>
        ) : null}
      </div>

      {categories.isSuccess && categories.data.length > 0 ? (
        <div
          role="group"
          aria-label="Filter by category"
          // Wraps at every width, so on a 320 px phone every category,
          // including the last, is fully visible and tappable (a sideways
          // scrolling row cut the last chip off with no hint it scrolled).
          className="flex flex-wrap gap-2"
        >
          <CategoryChip
            label="All"
            pressed={category === null}
            onClick={() => onCategoryChange(null)}
          />
          {categories.data.map((name) => (
            <CategoryChip
              key={name}
              label={name}
              pressed={category === name}
              onClick={() => onCategoryChange(category === name ? null : name)}
            />
          ))}
        </div>
      ) : null}

      {categories.isError ? (
        <p className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
          Categories could not be loaded.
          <Button
            type="button"
            variant="link"
            className="h-11 px-0"
            onClick={() => void categories.refetch()}
          >
            Retry
          </Button>
        </p>
      ) : null}
    </div>
  );
}
