/** The demo's documents (demo/, served by GET /api/demo/files), as files for setInputFiles. */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const DEMO = fileURLToPath(new URL("../../demo/", import.meta.url));
const file = (name: string, mimeType: string) => ({ name, mimeType, buffer: readFileSync(DEMO + name) });

export const SAMPLES = {
  agreement: file("Riverside Literacy Grant Agreement.pdf", "application/pdf"),
  signInPdf: file("Spring workshop sign-in sheets (signed).pdf", "application/pdf"),
  signIn: file("Spring workshop sign-in sheets (register).csv", "text/csv"),
  budget: file("Spring workshop budget report.csv", "text/csv"),
  report: file("Riverside Reads final outcomes report.pdf", "application/pdf"),
  reportDraft: file("Riverside Reads final outcomes report (draft).pdf", "application/pdf"),
  thin: file("Workshop photos note.txt", "text/plain"),
};
