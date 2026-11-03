import { formatHex, parse, wcagContrast } from "culori";
import { minimum, PAIRS, PALETTE, THEMES } from "./palette";

test.each(THEMES)("every declared pair meets its minimum in %s", (theme) => {
  const failures = PAIRS.filter((p) => {
    const hex = (c: string) => formatHex(parse(c)!);
    return wcagContrast(hex(PALETTE[theme][p.fg]), hex(PALETTE[theme][p.bg])) < minimum(p.kind, theme);
  }).map((p) => `${p.fg} on ${p.bg}`);
  expect(failures).toEqual([]);
});

test("every theme defines the same tokens", () => {
  const keys = (t: (typeof THEMES)[number]) => Object.keys(PALETTE[t]).sort();
  expect(keys("carbon")).toEqual(keys("paper"));
  expect(keys("contrast")).toEqual(keys("paper"));
});
