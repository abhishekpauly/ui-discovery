export async function fetchCustomers() {
  return fetch("/api/customers").then((r) => r.json());
}

export async function createCustomer(body) {
  return fetch("/api/customers", { method: "POST", body });
}

export async function fetchCustomer(id) {
  return fetch(`/api/customers/${id}`);
}
