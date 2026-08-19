import axios from "axios";

export default function Orders() {
  const load = () => axios.get("/api/orders");
  const one = (id) => axios.get(`/api/orders/${id}`);
  return (
    <main>
      <h1>Orders</h1>
      <button data-testid="new-order" aria-label="New order">New order</button>
    </main>
  );
}
