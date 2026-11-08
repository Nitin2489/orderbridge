"use client";

import { AlertCircleIcon, PackageSearchIcon } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { SignOutButton } from "@/features/auth/components/sign-out-button";
import { useSession } from "@/features/auth/session-provider";
import { useDebouncedValue } from "@/lib/use-debounced-value";

import { SEARCH_MAX_LENGTH, useCatalogue, type CatalogueFilters as Filters } from "../api";
import { catalogueErrorMessage } from "../errors";
import { formatCount } from "../format";
import { CatalogueFilters } from "./catalogue-filters";
import { ProductCard, ProductCardSkeletonList } from "./product-card";

const SEARCH_DEBOUNCE_MS = 300;

function EmptyState({ filtered, onClear }: { filtered: boolean; onClear: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
      <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
        <PackageSearchIcon className="size-6" aria-hidden="true" />
      </span>
      <h2 className="text-lg font-semibold">
        {filtered ? "No products match" : "No products available yet"}
      </h2>
      <p className="max-w-sm text-sm text-muted-foreground">
        {filtered
          ? "Try a different search or category."
          : "Your distributor has not made any products available to you. Please contact the order desk."}
      </p>
      {filtered ? (
        <Button type="button" variant="outline" className="h-11" onClick={onClear}>
          Clear search and filters
        </Button>
      ) : null}
    </div>
  );
}

/** The signed-in retailer's catalogue: search, category filter, product grid. */
export function CatalogueScreen() {
  const { user } = useSession();
  const [searchInput, setSearchInput] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const search = useDebouncedValue(
    searchInput.trim().slice(0, SEARCH_MAX_LENGTH),
    SEARCH_DEBOUNCE_MS,
  );

  const filters = useMemo<Filters>(() => ({ search, category }), [search, category]);
  const catalogue = useCatalogue(filters);

  const pages = catalogue.data?.pages ?? [];
  const products = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;
  const filtered = filters.search !== "" || filters.category !== null;
  // Results for a previous search stay visible while the new one loads.
  const updating =
    catalogue.isPlaceholderData ||
    (catalogue.isFetching && !catalogue.isFetchingNextPage && !catalogue.isPending);

  const clearFilters = () => {
    setSearchInput("");
    setCategory(null);
  };

  let body: ReactNode;
  if (catalogue.isPending) {
    body = (
      <>
        <p role="status" className="sr-only">
          Loading products…
        </p>
        <ProductCardSkeletonList count={6} />
      </>
    );
  } else if (catalogue.isError && products.length === 0) {
    body = (
      <Alert variant="destructive" role="alert">
        <AlertCircleIcon aria-hidden="true" />
        <AlertTitle>{catalogueErrorMessage(catalogue.error)}</AlertTitle>
        <AlertDescription>
          <p>Check your connection and try again.</p>
          <Button
            type="button"
            variant="outline"
            className="mt-3 h-11 text-foreground"
            onClick={() => void catalogue.refetch()}
          >
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  } else if (products.length === 0) {
    body = <EmptyState filtered={filtered} onClear={clearFilters} />;
  } else {
    body = (
      <div className="flex flex-col gap-6">
        <ul
          aria-label="Products"
          aria-busy={updating || undefined}
          className={`grid gap-4 transition-opacity sm:grid-cols-2 lg:grid-cols-3 ${updating ? "opacity-60" : ""}`}
        >
          {products.map((product) => (
            <li key={product.id}>
              <ProductCard product={product} />
            </li>
          ))}
        </ul>

        {catalogue.isFetchNextPageError ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>
              More products could not be loaded. {catalogueErrorMessage(catalogue.error)}
            </AlertDescription>
          </Alert>
        ) : null}

        {catalogue.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="self-center"
            disabled={catalogue.isFetchingNextPage}
            onClick={() => void catalogue.fetchNextPage()}
          >
            {catalogue.isFetchingNextPage ? (
              <>
                <Spinner />
                Loading…
              </>
            ) : catalogue.isFetchNextPageError ? (
              "Try loading more again"
            ) : (
              "Show more products"
            )}
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          {user?.retailer ? (
            <p className="text-sm font-medium text-primary">{user.retailer.business_name}</p>
          ) : null}
          <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">Catalogue</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Your prices and current schemes. Prices exclude GST.
          </p>
        </div>
        <SignOutButton />
      </div>

      <CatalogueFilters
        search={searchInput}
        onSearchChange={setSearchInput}
        category={category}
        onCategoryChange={setCategory}
      />

      <p
        role="status"
        aria-live="polite"
        className="flex min-h-5 items-center gap-2 text-sm text-muted-foreground"
      >
        {catalogue.isSuccess && !updating ? (
          products.length < total
            ? `Showing ${formatCount(products.length)} of ${formatCount(total)} products`
            : `${formatCount(total)} ${total === 1 ? "product" : "products"}`
        ) : null}
        {updating ? (
          <>
            <Spinner />
            Updating results…
          </>
        ) : null}
      </p>

      {body}
    </div>
  );
}
