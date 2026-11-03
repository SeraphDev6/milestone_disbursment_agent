/** The demo page's sample evidence, as files for setInputFiles (mirrors src/features/demo/seed.ts). */
const csv = (name: string, content: string) => ({ name, mimeType: "text/csv", buffer: Buffer.from(content) });
const txt = (name: string, content: string) => ({ name, mimeType: "text/plain", buffer: Buffer.from(content) });

export const SAMPLES = {
  signIn: csv("Spring sign-in sheets.csv", "name,session\n" + Array.from({ length: 214 }, (_, i) => `Person ${i},S${i % 4}`).join("\n")),
  budget: csv("Q2 budget actuals.csv", 'line,actual\nVenue,"$4,200.00"\nBooks,3100\nFacilitators,2500.50\n'),
  thin: txt("Workshop photos note.txt", "The workshops went really well and lots of people came. Everyone was engaged."),
};
