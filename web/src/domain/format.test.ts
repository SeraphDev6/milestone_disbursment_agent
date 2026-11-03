import { actionText, actorText, parseActor } from "./actors";
import { amount, date, measureText, money, shortHash } from "./format";
import { fieldId, humanPath, issuesFor, parsePath } from "./lint-paths";

test("money keeps cents and groups thousands", () => {
  expect(amount("20000.00")).toBe("20,000.00");
  expect(amount("4200.5")).toBe("4,200.50");
  expect(money("10000", "USD")).toBe("USD 10,000.00");
  expect(amount(null)).toBe("—");
});

test("calendar dates don't shift with the time zone", () => {
  expect(date("2027-05-31")).toBe("31 May 2027");
});

test("measures read as sentences", () => {
  expect(measureText({ comparator: "at_least", target: 200, unit: "people" })).toBe("at least 200 people");
  expect(measureText({ comparator: "exists" })).toBe("exists");
  expect(measureText({ comparator: "on_or_before", target: "2027-09-30" })).toBe("on or before 30 Sept 2027".replace("Sept", new Intl.DateTimeFormat("en-GB", { month: "short" }).format(new Date(2027, 8, 30))));
});

test("hashes shorten but keep both ends", () => {
  expect(shortHash("9f2c41d8a0b7e6c5d4f3", 4, 4)).toBe("9f2c…d4f3");
});

test("lint paths map to field ids and back", () => {
  expect(fieldId("milestones[0].criteria[1].statement")).toBe("f-milestones-0-criteria-1-statement");
  expect(parsePath("milestones[2].criteria[0].measure.target")).toEqual({ milestone: 2, criterion: 0, field: "measure.target" });
  expect(humanPath("milestones[0].criteria[1].evidence_description")).toBe("Tranche 1, criterion 2, evidence description");
  const issues = [
    { level: "warning" as const, code: "compound", path: "milestones[0].criteria[1].statement", message: "w" },
    { level: "error" as const, code: "vague_term", path: "milestones[0].criteria[1].statement", message: "e" },
    { level: "error" as const, code: "x", path: "milestones[1].criteria[0].statement", message: "other" },
  ];
  expect(issuesFor(issues, 0, 1).map((i) => i.message)).toEqual(["e", "w"]);
});

test("audit actors become people and orgs", () => {
  const names = { users: { u1: "a@harbor.org" }, orgs: { o1: "Harbor" } };
  expect(actorText("user:u1@org:o1", names)).toBe("a@harbor.org, Harbor");
  expect(parseActor("webhook:paypal").kind).toBe("paypal");
  expect(parseActor("github:octocat").label).toBe("GitHub · octocat");
  expect(actionText("milestones_replaced")).toBe("Milestones replaced");
});
