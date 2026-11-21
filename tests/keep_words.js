// Runs app.js's subjectHas over the shared vectors (tests/keep_words.json) and prints the results as JSON.
// Called by tests/test_keep_words.py; app.js is a browser script, so only that one function is taken from it.
const fs = require("fs"), path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "app.js"), "utf8");
const m = js.match(/function subjectHas\(words, subject\) \{[\s\S]*?\n\}\n/);
if (!m) { console.error("subjectHas not found in app.js"); process.exit(2); }
const subjectHas = new Function(`${m[0]}; return subjectHas;`)();
const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, "keep_words.json"), "utf8"));
process.stdout.write(JSON.stringify(vectors.map((v) => subjectHas(v.words, v.subject))));
