import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement layout APIs that components call.
Element.prototype.scrollIntoView ??= function () {};
window.matchMedia ??= (q: string) =>
  ({ matches: false, media: q, onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false }) as MediaQueryList;
