import { ApiError, locToPath } from "./errors";

test("string detail becomes the message", () => {
  expect(new ApiError(409, "tranche already has a live hold").message).toBe("Tranche already has a live hold");
});

test("structured detail keeps the lint report and current hash", () => {
  const e = new ApiError(409, { message: "criteria changed since you reviewed them", current_criteria_hash: "abc" });
  expect(e.message).toBe("Criteria changed since you reviewed them");
  expect(e.currentCriteriaHash).toBe("abc");
  const l = new ApiError(422, { message: "criteria have lint errors", lint: { ok: false, issues: [] } });
  expect(l.lint?.ok).toBe(false);
});

test("validation errors map to field paths", () => {
  const e = new ApiError(422, [{ loc: ["body", "milestones", 0, "criteria", 1, "statement"], msg: "Value error, too short" }]);
  expect(e.fields).toEqual([{ path: "milestones[0].criteria[1].statement", message: "too short" }]);
  expect(locToPath(["body", "title"])).toBe("title");
});

test("network failures say so", () => {
  expect(new ApiError(0, null).isNetwork).toBe(true);
});
