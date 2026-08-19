import Customers from "./pages/Customers";
import Orders from "./pages/Orders";
import About from "./pages/About";

export const routes = [
  { path: "/", component: Home },
  { path: "/customers", component: Customers },
  { path: "/orders", component: Orders },
  { path: "/orders/:orderId", component: OrderDetail },
  { path: "/about", component: About },
];
