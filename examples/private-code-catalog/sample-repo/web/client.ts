/** Tiny TypeScript client fixture, not a live HTTP request. */
export function orderPath(orderId: string): string {
  return "/orders/" + encodeURIComponent(orderId);
}
