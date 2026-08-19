import { fetchCustomers } from "../api/client";

// A page component. The extractor should find it, its labels and its test id.
export default function Customers() {
  const rows = fetchCustomers();
  return (
    <main>
      <h1>Customers</h1>
      <button data-testid="add-customer" aria-label="Go to customers">Go to customers</button>
      <a href="/orders">Orders</a>
    </main>
  );
}
