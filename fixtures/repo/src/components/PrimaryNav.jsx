// Shared chrome. Its labels appear on every crawled page, which makes it a
// good test of the "ambiguous label" path when two components share a word.
export const PrimaryNav = () => (
  <nav aria-label="Primary">
    <a href="/">Home</a>
    <a href="/customers">Customers</a>
    <a href="/orders">Orders</a>
    <a href="/about">About</a>
  </nav>
);
